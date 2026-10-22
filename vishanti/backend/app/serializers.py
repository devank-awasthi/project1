from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Attachment, ComplaintCluster, EscalationRule, LedgerEntry, Ticket, User


def _iso(dt):
    return dt.isoformat() if dt else None


async def ticket_out(session: AsyncSession, t: Ticket, *, staff_view: bool = False) -> dict:
    rule = await session.scalar(
        select(EscalationRule).where(EscalationRule.category == t.category, EscalationRule.level == t.current_level)
    )
    ladder = (
        await session.scalars(
            select(EscalationRule).where(EscalationRule.category == t.category).order_by(EscalationRule.level)
        )
    ).all()
    assignee = await session.get(User, t.assignee_id) if t.assignee_id else None
    files = (await session.scalars(select(Attachment).where(Attachment.ticket_id == t.id))).all()
    out = {
        "id": str(t.id),
        "ref": t.ref,
        "title": t.title,
        "description": t.description,
        "category": t.category,
        "priority": t.priority,
        "status": t.status,
        "level": t.current_level,
        "handler": rule.handler_designation if rule else None,
        "ladder": [
            {"level": r.level, "designation": r.handler_designation, "sla_hours": r.base_sla_hours} for r in ladder
        ],
        "assignee": (
            {"id": str(assignee.id), "designation": assignee.designation, "name": assignee.full_name}
            if assignee
            else None
        ),
        "anonymous": t.anon_token_hash is not None,
        "due_at": _iso(t.due_at),
        "timer_started_at": _iso(t.timer_started_at),
        "breach_count": t.breach_count,
        "ombudsperson_alerted_at": _iso(t.ombudsperson_alerted_at),
        "resolved_at": _iso(t.resolved_at),
        "confirm_by": _iso(t.confirm_by),
        "closed_at": _iso(t.closed_at),
        "created_at": _iso(t.created_at),
        "updated_at": _iso(t.updated_at),
        "head_hash": t.head_hash,
        "head_seq": t.head_seq,
        "attachments": [
            {
                "id": str(a.id),
                "filename": a.filename,
                "content_type": a.content_type,
                "size": a.size_bytes,
                "sha256": a.sha256,
            }
            for a in files
        ],
    }
    if staff_view:
        cluster = await session.get(ComplaintCluster, t.cluster_id) if t.cluster_id else None
        out["sentiment"] = t.sentiment
        out["cluster"] = (
            {"id": str(cluster.id), "size": cluster.size, "label": cluster.label}
            if cluster and cluster.size > 1
            else None
        )
    return out


def ticket_summary(t: Ticket) -> dict:
    return {
        "id": str(t.id),
        "ref": t.ref,
        "title": t.title,
        "category": t.category,
        "priority": t.priority,
        "status": t.status,
        "level": t.current_level,
        "due_at": _iso(t.due_at),
        "breach_count": t.breach_count,
        "anonymous": t.anon_token_hash is not None,
        "assignee_id": str(t.assignee_id) if t.assignee_id else None,
        "cluster_id": str(t.cluster_id) if t.cluster_id else None,
        "ombudsperson": t.ombudsperson_alerted_at is not None,
        "created_at": _iso(t.created_at),
        "updated_at": _iso(t.updated_at),
    }


def entry_out(e: LedgerEntry) -> dict:
    return {
        "seq": e.seq,
        "action": e.action,
        "actor": e.actor,
        "from": e.from_status,
        "to": e.to_status,
        "payload": e.payload,
        "payload_hash": e.payload_hash,
        "prev_hash": e.prev_hash,
        "hash": e.hash,
        "ts": e.ts,
    }
