from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import DESIGNATION_LEVEL
from app.db import SessionLocal, get_session
from app.deps import require_admin
from app.enums import OPEN_STATUSES, Role
from app.models import (
    AccessLog,
    Checkpoint,
    EscalationRule,
    TamperAlert,
    Ticket,
    TransitionRule,
    User,
    VerificationRun,
)
from app.schemas import OfficialIn, RuleIn
from app.security import hash_password
from app.serializers import ticket_summary
from app.services import escalation, events, ledger
from app.services.embeddings import backend_name

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _rule_out(r: EscalationRule) -> dict:
    return {
        "id": r.id,
        "category": r.category,
        "level": r.level,
        "handler_designation": r.handler_designation,
        "base_sla_hours": r.base_sla_hours,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


@router.get("/rules")
async def list_rules(_: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    rules = (
        await session.scalars(select(EscalationRule).order_by(EscalationRule.category, EscalationRule.level))
    ).all()
    transitions = (await session.scalars(select(TransitionRule).order_by(TransitionRule.id))).all()
    return {
        "escalation_rules": [_rule_out(r) for r in rules],
        "transitions": [
            {
                "from": t.from_state,
                "action": t.action,
                "actor": t.actor_kind,
                "to": t.to_state,
                "requires_assignee": t.requires_assignee,
                "description": t.description,
            }
            for t in transitions
        ],
        "designations": DESIGNATION_LEVEL,
    }


@router.put("/rules")
async def upsert_rule(body: RuleIn, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    """Policy changes are data changes: no redeploy. New SLAs apply to the
    next timer that starts; running timers are not silently moved."""
    if body.level > 1 and not await session.scalar(
        select(EscalationRule.id).where(
            EscalationRule.category == body.category, EscalationRule.level == body.level - 1
        )
    ):
        raise HTTPException(422, f"define level {body.level - 1} for {body.category} first")
    rule = await session.scalar(
        select(EscalationRule).where(EscalationRule.category == body.category, EscalationRule.level == body.level)
    )
    before = _rule_out(rule) if rule else None
    if rule is None:
        rule = EscalationRule(category=body.category, level=body.level)
        session.add(rule)
    rule.handler_designation = body.handler_designation
    rule.base_sla_hours = body.base_sla_hours
    await session.flush()
    session.add(
        AccessLog(
            user_id=admin.id,
            designation="admin",
            action="rule_change",
            detail={"before": before, "after": {k: v for k, v in body.model_dump().items()}},
        )
    )
    await session.commit()
    await session.refresh(rule)  # updated_at is set by the database
    return _rule_out(rule)


@router.get("/officials")
async def officials(_: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    load = (
        select(Ticket.assignee_id, func.count(Ticket.id))
        .where(Ticket.status.in_(OPEN_STATUSES))
        .group_by(Ticket.assignee_id)
    )
    loads = {r[0]: r[1] for r in await session.execute(load)}
    users = (
        await session.scalars(select(User).where(User.role == Role.OFFICIAL).order_by(User.level, User.designation))
    ).all()
    return [
        {
            "id": str(u.id),
            "email": u.email,
            "full_name": u.full_name,
            "designation": u.designation,
            "level": u.level,
            "active": u.is_active,
            "open_assigned": loads.get(u.id, 0),
        }
        for u in users
    ]


@router.post("/officials", status_code=201)
async def create_official(
    body: OfficialIn, _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)
):
    if await session.scalar(select(User.id).where(User.email == body.email.lower())):
        raise HTTPException(409, "email already in use")
    u = User(
        email=body.email.lower(),
        password_hash=hash_password(body.password),
        full_name=body.full_name,
        role=Role.OFFICIAL,
        designation=body.designation,
        level=body.level,
    )
    session.add(u)
    await session.commit()
    return {"id": str(u.id)}


@router.get("/integrity")
async def integrity(_: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    runs = (await session.scalars(select(VerificationRun).order_by(VerificationRun.id.desc()).limit(10))).all()
    alerts = (await session.scalars(select(TamperAlert).order_by(TamperAlert.id.desc()).limit(50))).all()
    cps = (await session.scalars(select(Checkpoint).order_by(Checkpoint.id.desc()).limit(14))).all()
    return {
        "embedding_backend": backend_name(),
        "overdue_now": await escalation.overdue_count(session),
        "runs": [
            {
                "id": r.id,
                "started_at": r.started_at.isoformat(),
                "duration_ms": int((r.finished_at - r.started_at).total_seconds() * 1000),
                "tickets": r.tickets_checked,
                "entries": r.entries_checked,
                "failures": r.failures,
                "checkpoints_ok": r.checkpoints_ok,
                "triggered_by": r.triggered_by,
            }
            for r in runs
        ],
        "alerts": [
            {
                "id": a.id,
                "ticket_id": str(a.ticket_id) if a.ticket_id else None,
                "ref": a.ticket_ref,
                "reason": a.reason,
                "detail": a.detail,
                "seq": a.failure_seq,
                "at": a.created_at.isoformat(),
                "acknowledged": a.acknowledged,
            }
            for a in alerts
        ],
        "checkpoints": [
            {
                "id": c.id,
                "day": c.day.isoformat(),
                "root_hash": c.root_hash,
                "prev_root": c.prev_root,
                "max_entry_id": c.max_entry_id,
                "tickets": c.ticket_count,
                "anchor_url": c.anchor_url,
            }
            for c in cps
        ],
    }


@router.post("/integrity/verify")
async def run_verify(admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    run = await ledger.verify_all(session, triggered_by=f"admin:{admin.email}")
    await events.commit(session)
    return {
        "run_id": run.id,
        "tickets": run.tickets_checked,
        "entries": run.entries_checked,
        "failures": run.failures,
        "checkpoints_ok": run.checkpoints_ok,
    }


@router.post("/integrity/checkpoint")
async def make_checkpoint(_: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    cp = await ledger.create_checkpoint(session)
    await events.commit(session)
    return {"id": cp.id, "root_hash": cp.root_hash, "anchor_url": cp.anchor_url, "tickets": cp.ticket_count}


@router.post("/alerts/{alert_id}/ack")
async def ack_alert(alert_id: int, _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    alert = await session.get(TamperAlert, alert_id)
    if alert is None:
        raise HTTPException(404)
    alert.acknowledged = True
    await session.commit()
    return {"ok": True}


@router.post("/sweep")
async def sweep_now(_: User = Depends(require_admin)):
    """Run the SLA sweeper once, now (the same code the scheduler runs)."""
    counts = await escalation.sweep(SessionLocal)
    warned = await escalation.warn_upcoming(SessionLocal)
    return {**counts, "warned": warned}


@router.get("/tickets")
async def all_tickets(
    status: str | None = None,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    q = select(Ticket).order_by(Ticket.created_at.desc()).limit(300)
    if status:
        q = q.where(Ticket.status == status)
    return [ticket_summary(t) for t in (await session.scalars(q)).all()]


@router.get("/access-logs")
async def access_logs(
    ticket_id: str | None = None, _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)
):
    q = select(AccessLog).order_by(AccessLog.id.desc()).limit(300)
    if ticket_id:
        q = q.where(AccessLog.ticket_id == ticket_id)
    rows = (await session.scalars(q)).all()
    return [
        {
            "id": r.id,
            "user_id": str(r.user_id),
            "designation": r.designation,
            "ticket_id": str(r.ticket_id) if r.ticket_id else None,
            "action": r.action,
            "detail": r.detail,
            "at": r.created_at.isoformat(),
        }
        for r in rows
    ]
