"""Notifications: persisted (for replay) and published to Redis after commit."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification, Ticket
from app.services.events import after_commit

ADMIN_CHANNEL = "admin"


def petitioner_channel(ticket: Ticket) -> str:
    if ticket.petitioner_id is not None:
        return f"user:{ticket.petitioner_id}"
    # Derived from the stored hash. Subscribing still requires the raw token,
    # because the stream endpoint hashes what the client presents.
    return f"anon:{ticket.anon_token_hash}"


def user_channel(user_id) -> str:
    return f"user:{user_id}"


def queue_channel(designation: str) -> str:
    return f"queue:{designation}"


def ticket_event(ticket: Ticket, message: str, **extra) -> dict:
    return {
        "ticket_id": str(ticket.id),
        "ref": ticket.ref,
        "status": ticket.status,
        "level": ticket.current_level,
        "due_at": ticket.due_at.isoformat() if ticket.due_at else None,
        "message": message,
        **extra,
    }


async def notify(session: AsyncSession, channels: list[str], event_type: str, payload: dict) -> None:
    rows = [Notification(channel=c, event_type=event_type, payload=payload) for c in dict.fromkeys(channels) if c]
    session.add_all(rows)
    await session.flush()  # assigns ids, which become SSE event ids
    for row in rows:
        after_commit(
            session,
            "publish",
            channel=row.channel,
            message={"id": row.id, "event": event_type, "data": payload},
        )
