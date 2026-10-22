from fastapi import APIRouter, Depends
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import require_official
from app.enums import OPEN_STATUSES, TicketStatus
from app.models import AccessLog, ComplaintCluster, EscalationRule, Ticket, User
from app.serializers import ticket_summary

router = APIRouter(prefix="/api/official", tags=["official"])


async def _my_categories(session: AsyncSession, user: User) -> list[str]:
    return list(
        (
            await session.scalars(
                select(EscalationRule.category).where(
                    EscalationRule.handler_designation == user.designation, EscalationRule.level == user.level
                )
            )
        ).all()
    )


@router.get("/queue")
async def queue(user: User = Depends(require_official), session: AsyncSession = Depends(get_session)):
    """Tickets this official can pick up (unassigned at their level in their
    categories) plus everything currently assigned to them."""
    cats = await _my_categories(session, user)
    rows = (
        await session.scalars(
            select(Ticket)
            .where(
                or_(
                    Ticket.assignee_id == user.id,
                    (Ticket.category.in_(cats))
                    & (Ticket.current_level == user.level)
                    & (Ticket.status.in_([TicketStatus.FILED, TicketStatus.ESCALATED])),
                ),
                Ticket.status.in_([*OPEN_STATUSES, TicketStatus.RESOLVED]),
            )
            .order_by(Ticket.due_at.asc().nulls_last())
            .limit(300)
        )
    ).all()
    session.add(
        AccessLog(user_id=user.id, designation=user.designation, action="list_queue", detail={"count": len(rows)})
    )
    await session.commit()
    return {
        "categories": cats,
        "level": user.level,
        "designation": user.designation,
        "tickets": [ticket_summary(t) for t in rows],
    }


@router.get("/clusters")
async def clusters(user: User = Depends(require_official), session: AsyncSession = Depends(get_session)):
    cats = await _my_categories(session, user)
    rows = (
        await session.scalars(
            select(ComplaintCluster)
            .where(ComplaintCluster.category.in_(cats), ComplaintCluster.size > 1)
            .order_by(ComplaintCluster.size.desc())
            .limit(50)
        )
    ).all()
    out = []
    for c in rows:
        members = (
            await session.scalars(
                select(Ticket).where(Ticket.cluster_id == c.id).order_by(Ticket.created_at.desc()).limit(20)
            )
        ).all()
        out.append(
            {
                "id": str(c.id),
                "category": c.category,
                "label": c.label,
                "size": c.size,
                "avg_sentiment": round(c.sentiment_sum / max(c.size, 1), 2),
                "systemic": c.systemic_alerted,
                "tickets": [{"id": str(t.id), "ref": t.ref, "title": t.title, "status": t.status} for t in members],
            }
        )
    return out
