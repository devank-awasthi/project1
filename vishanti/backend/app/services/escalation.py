"""SLA enforcement.

Two independent mechanisms fire the same idempotent function:

1. A Celery task with an ETA equal to the ticket's `due_at`, queued after
   every commit that starts a timer. Fast and precise.
2. A sweeper (Celery beat, every minute) that finds overdue tickets with
   `FOR UPDATE SKIP LOCKED`. This is the backstop if Redis lost the task,
   a worker crashed, or the ETA task was delivered twice.

`escalate_if_due` re-reads the row under lock and only acts if the ticket is
still open and actually past its deadline. After it escalates, `due_at`
moves forward, so any duplicate or stale task finds "not due" and does
nothing. A ticket can never be escalated twice for the same deadline.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.actors import Actor
from app.canonical import format_ts, utcnow
from app.config import settings
from app.enums import OPEN_STATUSES, Action, TicketStatus
from app.models import Ticket
from app.services import events
from app.services.notify import notify, petitioner_channel, queue_channel, ticket_event, user_channel
from app.services.tickets import after_escalation, apply_locked, get_rule, lock_ticket, raise_ombudsperson_alert

log = logging.getLogger("vishanti.escalation")
SLA_WORKER = Actor.system("sla-worker")


async def escalate_if_due(
    session: AsyncSession, ticket_id: uuid.UUID, expected_due: str | None = None, now: datetime | None = None
) -> str:
    """Returns: escalated | ombudsperson | not_due | stale | not_open."""
    now = now or utcnow()
    ticket = await lock_ticket(session, ticket_id)
    if ticket.status not in OPEN_STATUSES:
        return "not_open"
    if expected_due is not None and (ticket.due_at is None or format_ts(ticket.due_at) != expected_due):
        return "stale"  # this task was for an older deadline
    if ticket.due_at is None or ticket.due_at > now:
        return "not_due"
    if ticket.ombudsperson_alerted_at is not None:
        return "not_open"

    next_rule = await get_rule(session, ticket.category, ticket.current_level + 1)
    if next_rule is None:
        # Already at the top authority and it breached too.
        ticket.breach_count += 1
        await raise_ombudsperson_alert(session, ticket, now, reason="SLA breached at the highest authority")
        return "ombudsperson"

    await apply_locked(session, ticket, Action.SLA_ESCALATE, SLA_WORKER, now)
    await after_escalation(session, ticket, now)
    return "escalated"


async def auto_close_if_due(session: AsyncSession, ticket_id: uuid.UUID, now: datetime | None = None) -> bool:
    now = now or utcnow()
    ticket = await lock_ticket(session, ticket_id)
    if ticket.status != TicketStatus.RESOLVED or ticket.confirm_by is None or ticket.confirm_by > now:
        return False
    await apply_locked(session, ticket, Action.AUTO_CLOSE, Actor.system("closer"), now)
    return True


async def _next_locked(session: AsyncSession, query) -> uuid.UUID | None:
    return await session.scalar(query.limit(1).with_for_update(skip_locked=True))


async def sweep(sessionmaker: async_sessionmaker, now: datetime | None = None, limit: int = 500) -> dict:
    """One ticket per transaction, so a failure on one ticket does not roll
    back the others, and parallel sweepers skip rows another one holds."""
    now = now or utcnow()
    counts = {"escalated": 0, "ombudsperson": 0, "auto_closed": 0, "errors": 0}

    overdue = (
        select(Ticket.id)
        .where(Ticket.status.in_(OPEN_STATUSES), Ticket.due_at <= now, Ticket.ombudsperson_alerted_at.is_(None))
        .order_by(Ticket.due_at)
    )
    to_close = select(Ticket.id).where(Ticket.status == TicketStatus.RESOLVED, Ticket.confirm_by <= now)

    for query, kind in ((overdue, "escalate"), (to_close, "close")):
        for _ in range(limit):
            async with sessionmaker() as session:
                try:
                    tid = await _next_locked(session, query)
                    if tid is None:
                        await session.rollback()
                        break
                    if kind == "escalate":
                        outcome = await escalate_if_due(session, tid, now=now)
                        if outcome in counts:
                            counts[outcome] += 1
                    elif await auto_close_if_due(session, tid, now=now):
                        counts["auto_closed"] += 1
                    await events.commit(session)
                except Exception:
                    log.exception("sweep failed on one ticket")
                    counts["errors"] += 1
                    await events.rollback(session)
                    break
    return counts


async def warn_upcoming(sessionmaker: async_sessionmaker, now: datetime | None = None, limit: int = 500) -> int:
    """'SLA breach in X minutes' once per deadline, when the remaining time
    drops below SLA_WARN_FRACTION of the timer."""
    now = now or utcnow()
    warned = 0
    query = select(Ticket).where(
        Ticket.status.in_(OPEN_STATUSES),
        Ticket.due_at > now,
        Ticket.ombudsperson_alerted_at.is_(None),
        (Ticket.sla_warned_for.is_(None)) | (Ticket.sla_warned_for != Ticket.due_at),
        (Ticket.due_at - now) <= (Ticket.due_at - Ticket.timer_started_at) * settings.SLA_WARN_FRACTION,
    )
    for _ in range(limit):
        async with sessionmaker() as session:
            ticket = await session.scalar(query.limit(1).with_for_update(skip_locked=True))
            if ticket is None:
                await session.rollback()
                break
            ticket.sla_warned_for = ticket.due_at
            minutes = max(1, int((ticket.due_at - now).total_seconds() // 60))
            rule = await get_rule(session, ticket.category, ticket.current_level)
            channels = [petitioner_channel(ticket), queue_channel(rule.handler_designation)]
            if ticket.assignee_id:
                channels.append(user_channel(ticket.assignee_id))
            await notify(
                session,
                channels,
                "sla_warning",
                ticket_event(
                    ticket,
                    f"SLA breach in {minutes} minutes: {ticket.ref} escalates past {rule.handler_designation}",
                    minutes_left=minutes,
                ),
            )
            await events.commit(session)
            warned += 1
    return warned


async def overdue_count(session: AsyncSession) -> int:
    return await session.scalar(
        select(func.count(Ticket.id)).where(
            Ticket.status.in_(OPEN_STATUSES), Ticket.due_at <= utcnow(), Ticket.ombudsperson_alerted_at.is_(None)
        )
    )
