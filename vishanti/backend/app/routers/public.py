from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import CATEGORIES
from app.db import ReadSessionLocal, get_read_session
from app.enums import PRIORITY_SCALE
from app.models import Checkpoint, EscalationRule, TransitionRule
from app.services import dashboard

router = APIRouter(prefix="/api/public", tags=["public"])


@router.get("/dashboard")
async def public_dashboard():
    return await dashboard.cached(ReadSessionLocal)


@router.get("/checkpoints")
async def checkpoints(session: AsyncSession = Depends(get_read_session)):
    rows = (await session.scalars(select(Checkpoint).order_by(Checkpoint.id.desc()).limit(60))).all()
    return [
        {
            "id": c.id,
            "day": c.day.isoformat(),
            "root_hash": c.root_hash,
            "prev_root": c.prev_root,
            "max_entry_id": c.max_entry_id,
            "tickets": c.ticket_count,
            "anchor_url": c.anchor_url,
        }
        for c in rows
    ]


@router.get("/meta")
async def meta(session: AsyncSession = Depends(get_read_session)):
    """Everything a client needs to render forms and explain the rules."""
    rules = (
        await session.scalars(select(EscalationRule).order_by(EscalationRule.category, EscalationRule.level))
    ).all()
    transitions = (await session.scalars(select(TransitionRule).order_by(TransitionRule.id))).all()
    ladders: dict[str, list] = {}
    for r in rules:
        ladders.setdefault(r.category, []).append(
            {"level": r.level, "designation": r.handler_designation, "sla_hours": r.base_sla_hours}
        )
    return {
        "categories": [c for c in CATEGORIES if c in ladders] + [c for c in ladders if c not in CATEGORIES],
        "priorities": {k: v for k, v in PRIORITY_SCALE.items()},
        "ladders": ladders,
        "transitions": [
            {
                "from": t.from_state,
                "action": t.action,
                "actor": t.actor_kind,
                "to": t.to_state,
                "description": t.description,
            }
            for t in transitions
        ],
    }
