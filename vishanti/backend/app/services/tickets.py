"""TicketService: creation and every state transition.

Each public function runs inside the caller's transaction and starts by
locking the ticket row (SELECT ... FOR UPDATE). The transition is looked up
in `transition_rules`, guarded by role/level/assignee, applied, and appended to
the ticket's hash chain, all before the caller commits. Two officials acting
on the same ticket are therefore serialised; unrelated tickets never block
each other.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actors import Actor
from app.canonical import format_ts, utcnow
from app.config import settings
from app.enums import PRIORITY_SCALE, Action, ActorKind, Role, TicketStatus
from app.models import Attachment, EscalationRule, Ticket, TransitionRule, User, ticket_ref_seq
from app.security import new_anonymous_token
from app.services import ledger
from app.services.events import queue_task
from app.services.notify import ADMIN_CHANNEL, notify, petitioner_channel, queue_channel, ticket_event, user_channel


class TicketError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@dataclass
class UploadedFile:
    upload_id: str
    object_key: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str


# ---------------------------------------------------------------- helpers


def sla_delta(base_hours: int, priority: str) -> timedelta:
    return timedelta(seconds=base_hours * PRIORITY_SCALE[priority] * settings.SLA_HOUR_SECONDS)


def confirm_window() -> timedelta:
    return timedelta(seconds=settings.RESOLUTION_CONFIRM_HOURS * settings.SLA_HOUR_SECONDS)


async def get_rule(session: AsyncSession, category: str, level: int) -> EscalationRule | None:
    return await session.scalar(
        select(EscalationRule).where(EscalationRule.category == category, EscalationRule.level == level)
    )


async def lock_ticket(session: AsyncSession, ticket_id: uuid.UUID) -> Ticket:
    ticket = await session.scalar(select(Ticket).where(Ticket.id == ticket_id).with_for_update())
    if ticket is None:
        raise TicketError(404, "ticket not found")
    return ticket


def is_petitioner(actor: Actor, ticket: Ticket) -> bool:
    if actor.kind == "anon":
        return ticket.anon_token_hash is not None and actor.anon_hash == ticket.anon_token_hash
    if actor.kind == "user" and actor.user.role == Role.STUDENT:
        return ticket.petitioner_id == actor.user.id
    return False


async def official_handles_category(session: AsyncSession, user: User, category: str) -> bool:
    """Any official on this category's escalation path may view it."""
    return bool(
        await session.scalar(
            select(EscalationRule.id).where(
                EscalationRule.category == category,
                EscalationRule.handler_designation == user.designation,
                EscalationRule.level == user.level,
            )
        )
    )


def _timer(ticket: Ticket, delta: timedelta, now: datetime) -> None:
    ticket.timer_started_at = now
    ticket.due_at = now + delta
    ticket.sla_warned_for = None


def _queue_escalation_task(session: AsyncSession, ticket: Ticket) -> None:
    if ticket.due_at is not None:
        queue_task(session, "vishanti.escalate_ticket", [str(ticket.id), format_ts(ticket.due_at)], eta=ticket.due_at)


# ---------------------------------------------------------------- create


async def find_by_idempotency_key(session: AsyncSession, key: str) -> Ticket | None:
    return await session.scalar(select(Ticket).where(Ticket.idempotency_key == key))


async def create_ticket(
    session: AsyncSession,
    *,
    actor: Actor | None,
    title: str,
    description: str,
    category: str,
    priority: str,
    anonymous: bool,
    idempotency_key: str,
    uploads: list[UploadedFile] | None = None,
    now: datetime | None = None,
) -> tuple[Ticket, str | None]:
    """Insert in FILED, start the SLA clock, write GENESIS. Returns the raw
    anonymous token (shown once) when filed anonymously."""
    now = now or utcnow()
    uploads = uploads or []
    rule = await get_rule(session, category, 1)
    if rule is None:
        raise TicketError(422, f"unknown category '{category}'")
    if priority not in PRIORITY_SCALE:
        raise TicketError(422, f"unknown priority '{priority}'")
    if not anonymous and (actor is None or actor.kind != "user" or actor.user.role != Role.STUDENT):
        raise TicketError(401, "log in as a student, or file anonymously")

    raw_token = token_hash = None
    if anonymous:
        # Identity is deliberately dropped even if the petitioner is logged in.
        raw_token, token_hash = new_anonymous_token()

    seq_val = await session.scalar(select(ticket_ref_seq.next_value()))
    ticket = Ticket(
        id=uuid.uuid4(),
        ref=f"VSH-{seq_val:06d}",
        title=title.strip(),
        description=description.strip(),
        category=category,
        priority=priority,
        status=TicketStatus.FILED,
        current_level=1,
        petitioner_id=None if anonymous else actor.user.id,
        anon_token_hash=token_hash,
        idempotency_key=idempotency_key,
        breach_count=0,
        head_seq=-1,
        created_at=now,
        updated_at=now,
    )
    _timer(ticket, sla_delta(rule.base_sla_hours, priority), now)
    session.add(ticket)
    await session.flush()

    files = []
    for up in uploads:
        a = Attachment(
            id=uuid.uuid4(),
            ticket_id=ticket.id,
            object_key=up.object_key,
            filename=up.filename,
            content_type=up.content_type,
            size_bytes=up.size_bytes,
            sha256=up.sha256,
        )
        session.add(a)
        files.append(a)
    await session.flush()

    await ledger.append(
        session,
        ticket,
        actor="petitioner:anonymous" if anonymous else actor.ledger_label,
        action=Action.GENESIS,
        payload={
            "from": None,
            "to": TicketStatus.FILED,
            "level": 1,
            "category": category,
            "priority": priority,
            "petitioner": "anonymous" if anonymous else "account",
            "content_sha256": ledger.content_hash(ticket.title, ticket.description, category, priority),
            "attachments": [{"id": str(a.id), "sha256": a.sha256} for a in files],
            "due_at": format_ts(ticket.due_at),
            "handler": rule.handler_designation,
        },
        now=now,
    )
    await notify(
        session,
        [petitioner_channel(ticket), queue_channel(rule.handler_designation)],
        "ticket_filed",
        ticket_event(ticket, f"{ticket.ref} filed in {category}; {rule.handler_designation} has the SLA clock"),
    )
    _queue_escalation_task(session, ticket)
    queue_task(session, "vishanti.embed_ticket", [str(ticket.id)])
    return ticket, raw_token


# ---------------------------------------------------------------- transitions


def _actor_kind(actor: Actor, ticket: Ticket) -> str:
    if actor.kind == "system":
        return ActorKind.SYSTEM
    if is_petitioner(actor, ticket):
        return ActorKind.PETITIONER
    if actor.is_official:
        return ActorKind.OFFICIAL
    raise TicketError(403, "you are not a party to this ticket")


async def _check_official(session: AsyncSession, actor: Actor, ticket: Ticket, rule: TransitionRule) -> None:
    user = actor.user
    level_rule = await get_rule(session, ticket.category, ticket.current_level)
    if level_rule is None or user.level != ticket.current_level or user.designation != level_rule.handler_designation:
        raise TicketError(
            403,
            f"only the {level_rule.handler_designation if level_rule else '?'} (level {ticket.current_level}) can do this",
        )
    if rule.requires_assignee and ticket.assignee_id != user.id:
        raise TicketError(403, "only the assigned official can do this")


async def pick_official(session: AsyncSession, designation: str, level: int) -> User | None:
    """Least-loaded active official holding this designation."""
    load = (
        select(func.count(Ticket.id))
        .where(Ticket.assignee_id == User.id, Ticket.status.in_([TicketStatus.ASSIGNED, TicketStatus.IN_REVIEW]))
        .correlate(User)
        .scalar_subquery()
    )
    return await session.scalar(
        select(User)
        .where(
            User.role == Role.OFFICIAL, User.is_active.is_(True), User.designation == designation, User.level == level
        )
        .order_by(load, User.created_at)
        .limit(1)
    )


async def _apply(
    session: AsyncSession,
    ticket: Ticket,
    *,
    action: str,
    actor: Actor,
    note: str | None,
    now: datetime,
    extra: dict | None = None,
) -> None:
    """Look up and fire one transition on an already-locked ticket."""
    kind = _actor_kind(actor, ticket)
    rule = await session.scalar(
        select(TransitionRule).where(
            TransitionRule.from_state == ticket.status,
            TransitionRule.action == action,
            TransitionRule.actor_kind == kind,
        )
    )
    if rule is None:
        raise TicketError(409, f"{action} is not allowed from {ticket.status} for {kind}")
    if kind == ActorKind.OFFICIAL:
        await _check_official(session, actor, ticket, rule)

    old = ticket.status
    payload: dict = {"from": old, "to": rule.to_state}
    channels = [petitioner_channel(ticket), ADMIN_CHANNEL]
    message = f"{ticket.ref}: {old} → {rule.to_state}"

    if action in (Action.ASSIGN, Action.AUTO_ASSIGN):
        assignee: User = actor.user if action == Action.ASSIGN else extra["assignee"]
        ticket.assignee_id = assignee.id
        payload["assignee"] = f"{assignee.designation}:{assignee.id}"
        channels.append(user_channel(assignee.id))
        message = f"{ticket.ref} assigned to {assignee.designation}"

    elif action == Action.START_REVIEW:
        message = f"{ticket.ref} is under review"

    elif action == Action.RESOLVE:
        if not note:
            raise TicketError(422, "a resolution note is required")
        ticket.resolved_at = now
        ticket.confirm_by = now + confirm_window()
        payload["confirm_by"] = format_ts(ticket.confirm_by)
        message = f"{ticket.ref} marked resolved. Confirm or dispute before {ticket.confirm_by:%d %b %H:%M} UTC"
        queue_task(session, "vishanti.auto_close_ticket", [str(ticket.id)], eta=ticket.confirm_by)

    elif action in (Action.CONFIRM, Action.AUTO_CLOSE):
        ticket.closed_at = now
        ticket.due_at = None
        ticket.confirm_by = None
        message = f"{ticket.ref} closed" + (" (no response in time)" if action == Action.AUTO_CLOSE else "")

    elif action in (Action.DISPUTE, Action.SLA_ESCALATE):
        if action == Action.DISPUTE and not note:
            raise TicketError(422, "say why the resolution is not acceptable")
        if action == Action.SLA_ESCALATE:
            ticket.breach_count += 1
            payload["breached_due_at"] = format_ts(ticket.due_at)
        next_rule = await get_rule(session, ticket.category, ticket.current_level + 1)
        if next_rule is not None:
            ticket.current_level += 1
            target = next_rule
        else:
            target = await get_rule(session, ticket.category, ticket.current_level)
        ticket.assignee_id = None
        ticket.resolved_at = None
        ticket.confirm_by = None
        _timer(ticket, sla_delta(target.base_sla_hours, ticket.priority), now)
        payload["handler"] = target.handler_designation
        payload["due_at"] = format_ts(ticket.due_at)
        channels.append(queue_channel(target.handler_designation))
        why = "petitioner disputed the resolution" if action == Action.DISPUTE else "SLA deadline passed"
        message = f"{ticket.ref} escalated to {target.handler_designation} (level {ticket.current_level}): {why}"

    if note:
        payload["note"] = note
    payload["level"] = ticket.current_level
    if ticket.assignee_id and action not in (Action.ASSIGN, Action.AUTO_ASSIGN):
        channels.append(user_channel(ticket.assignee_id))

    ticket.status = rule.to_state
    ticket.updated_at = now
    await ledger.append(session, ticket, actor=actor.ledger_label, action=action, payload=payload, now=now)
    await notify(session, channels, "status_changed", ticket_event(ticket, message, action=action))


async def after_escalation(session: AsyncSession, ticket: Ticket, now: datetime) -> None:
    """Route a just-escalated ticket to the least-loaded official at its new
    level (ESCALATED → ASSIGNED, recorded as a system entry) and queue the
    timer for its fresh deadline. If nobody holds that designation, it stays
    ESCALATED in that level's queue and the timer still runs."""
    rule = await get_rule(session, ticket.category, ticket.current_level)
    assignee = await pick_official(session, rule.handler_designation, ticket.current_level)
    if assignee is not None:
        await _apply(
            session,
            ticket,
            action=Action.AUTO_ASSIGN,
            actor=Actor.system("router"),
            note=None,
            now=now,
            extra={"assignee": assignee},
        )
    _queue_escalation_task(session, ticket)


async def transition(
    session: AsyncSession,
    ticket_id: uuid.UUID,
    action: str,
    actor: Actor,
    note: str | None = None,
    now: datetime | None = None,
) -> Ticket:
    now = now or utcnow()
    ticket = await lock_ticket(session, ticket_id)
    was_top = await get_rule(session, ticket.category, ticket.current_level + 1) is None
    await _apply(session, ticket, action=action, actor=actor, note=note, now=now)
    if action == Action.DISPUTE:
        if was_top:
            await raise_ombudsperson_alert(session, ticket, now, reason="petitioner disputed the top-level resolution")
        await after_escalation(session, ticket, now)
    return ticket


async def apply_locked(
    session: AsyncSession, ticket: Ticket, action: str, actor: Actor, now: datetime, note: str | None = None
) -> None:
    """For background workers that already hold the row lock."""
    await _apply(session, ticket, action=action, actor=actor, note=note, now=now)


async def raise_ombudsperson_alert(session: AsyncSession, ticket: Ticket, now: datetime, reason: str) -> None:
    ticket.ombudsperson_alerted_at = now
    ticket.updated_at = now
    await ledger.append(
        session,
        ticket,
        actor="system:sla-worker",
        action=Action.OMBUDSPERSON_ALERT,
        payload={"level": ticket.current_level, "reason": reason, "contact": settings.OMBUDSPERSON_CONTACT},
        now=now,
    )
    await notify(
        session,
        [petitioner_channel(ticket), ADMIN_CHANNEL],
        "ombudsperson_alert",
        ticket_event(ticket, f"{ticket.ref}: {reason}. External ombudsperson notified and the breach is public."),
    )


async def add_comment(session: AsyncSession, ticket_id: uuid.UUID, actor: Actor, body: str) -> Ticket:
    ticket = await lock_ticket(session, ticket_id)
    if not body.strip():
        raise TicketError(422, "empty comment")
    if not (
        is_petitioner(actor, ticket)
        or (actor.is_official and await official_handles_category(session, actor.user, ticket.category))
    ):
        raise TicketError(403, "only the petitioner or a handling official can comment")
    if ticket.status == TicketStatus.CLOSED:
        raise TicketError(409, "ticket is closed")
    await ledger.append(
        session, ticket, actor=actor.ledger_label, action=Action.COMMENT, payload={"text": body.strip()}
    )
    ticket.updated_at = utcnow()
    channels = [petitioner_channel(ticket)]
    if ticket.assignee_id:
        channels.append(user_channel(ticket.assignee_id))
    await notify(session, channels, "comment", ticket_event(ticket, f"New comment on {ticket.ref}"))
    return ticket
