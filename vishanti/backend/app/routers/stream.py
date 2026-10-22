"""Real-time notification hub (Server-Sent Events over Redis pub/sub).

EventSource cannot send an Authorization header, and putting a JWT or an
anonymous token in a URL would leak it into logs. So the client first POSTs
(with its normal credentials) to get a short-lived stream key that only maps
to a list of channels, then opens EventSource with that key.

Replay: every notification is stored in Postgres with a bigint id, which is
also the SSE event id. On reconnect the browser sends Last-Event-ID (or the
client passes ?last_event_id=), and we send everything it missed before
switching to live messages. We subscribe *before* replaying and drop live
messages with id <= the last one sent, so nothing falls in the gap.
"""

from __future__ import annotations

import asyncio
import json
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from app.actors import Actor
from app.db import SessionLocal
from app.deps import require_actor
from app.enums import Role
from app.models import Notification
from app.redis_client import get_redis
from app.services.notify import ADMIN_CHANNEL, queue_channel, user_channel

router = APIRouter(prefix="/api/stream", tags=["stream"])
KEY_TTL = 600


def channels_for(actor: Actor) -> list[str]:
    if actor.kind == "anon":
        return [f"anon:{actor.anon_hash}"]
    u = actor.user
    chans = [user_channel(u.id)]
    if u.role == Role.OFFICIAL and u.designation:
        chans.append(queue_channel(u.designation))
    if u.role == Role.ADMIN:
        chans.append(ADMIN_CHANNEL)
    return chans


@router.post("/key")
async def stream_key(actor: Actor = Depends(require_actor)):
    key = secrets.token_urlsafe(24)
    chans = channels_for(actor)
    await get_redis().set(f"streamkey:{key}", json.dumps(chans), ex=KEY_TTL)
    return {"key": key, "expires_in": KEY_TTL, "channels": len(chans)}


@router.get("")
async def stream(
    request: Request,
    key: str = Query(min_length=16),
    last_event_id: int | None = Query(default=None),
    last_event_id_header: str | None = Header(default=None, alias="Last-Event-ID"),
):
    raw = await get_redis().get(f"streamkey:{key}")
    if not raw:
        raise HTTPException(401, "stream key expired; request a new one")
    channels: list[str] = json.loads(raw)
    since = last_event_id
    if since is None and last_event_id_header and last_event_id_header.isdigit():
        since = int(last_event_id_header)

    async def events():
        pubsub = get_redis().pubsub()
        await pubsub.subscribe(*[f"vishanti:{c}" for c in channels])
        last = since or 0
        try:
            if since:
                async with SessionLocal() as session:
                    missed = (
                        await session.scalars(
                            select(Notification)
                            .where(Notification.channel.in_(channels), Notification.id > since)
                            .order_by(Notification.id)
                            .limit(500)
                        )
                    ).all()
                for n in missed:
                    last = n.id
                    yield {"id": str(n.id), "event": n.event_type, "data": json.dumps({**n.payload, "replayed": True})}
            yield {"event": "ready", "data": json.dumps({"channels": len(channels), "replayed_from": since})}
            while True:
                if await request.is_disconnected():
                    break
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
                if msg is None:
                    await asyncio.sleep(0)
                    continue
                body = json.loads(msg["data"])
                if body["id"] <= last:
                    continue
                last = body["id"]
                yield {"id": str(body["id"]), "event": body["event"], "data": json.dumps(body["data"])}
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()

    return EventSourceResponse(events(), ping=15, headers={"X-Accel-Buffering": "no"})
