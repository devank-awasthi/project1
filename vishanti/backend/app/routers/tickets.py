from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.actors import Actor
from app.canonical import sha256_hex
from app.db import get_session
from app.deps import optional_actor, require_actor, require_student
from app.models import AccessLog, Attachment, LedgerEntry, Ticket, User
from app.ratelimit import limiter
from app.redis_client import get_redis
from app.schemas import ActionIn, CommentIn, PresignIn, TicketIn
from app.serializers import entry_out, ticket_out, ticket_summary
from app.services import events, ledger, storage
from app.services.tickets import (
    TicketError,
    add_comment,
    create_ticket,
    find_by_idempotency_key,
    is_petitioner,
    official_handles_category,
    transition,
)

log = logging.getLogger("vishanti.api")
router = APIRouter(tags=["tickets"])
IDEMPOTENT_REPLAY_SECONDS = 900


async def _cache_get(key: str) -> dict | None:
    try:
        raw = await get_redis().get(key)
        return json.loads(raw) if raw else None
    except Exception:
        return None


async def _cache_set(key: str, value: dict) -> None:
    try:
        await get_redis().set(key, json.dumps(value), ex=IDEMPOTENT_REPLAY_SECONDS)
    except Exception as exc:
        log.warning("idempotency cache unavailable: %s", exc)


async def load_visible_ticket(
    session: AsyncSession, ticket_id: uuid.UUID, actor: Actor, log_action: str
) -> tuple[Ticket, bool]:
    """Returns (ticket, staff_view). Every staff read is written to access_logs."""
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(404, "ticket not found")
    if is_petitioner(actor, ticket):
        return ticket, False
    if actor.user is not None and (
        actor.is_admin or (actor.is_official and await official_handles_category(session, actor.user, ticket.category))
    ):
        session.add(
            AccessLog(
                user_id=actor.user.id,
                designation=actor.user.designation or actor.user.role,
                ticket_id=ticket.id,
                action=log_action,
                detail={"ref": ticket.ref},
            )
        )
        await session.commit()
        return ticket, True
    raise HTTPException(404, "ticket not found")  # don't reveal that it exists


# ------------------------------------------------------------------ uploads


@router.post("/api/uploads", status_code=201)
@limiter.limit("30/minute")
async def presign_upload(request: Request, body: PresignIn):
    return await storage.presign(body.filename, body.content_type, body.size)


@router.put("/api/uploads/{upload_id}/blob", status_code=204)
async def local_blob(upload_id: str, request: Request):
    from app.config import settings

    if settings.STORAGE_BACKEND != "local":
        raise HTTPException(404)
    data = await request.body()
    await storage.receive_local_blob(upload_id, data)
    return Response(status_code=204)


@router.post("/api/uploads/{upload_id}/finalize")
async def finalize_upload(upload_id: str):
    return await storage.finalize(upload_id)


# ------------------------------------------------------------------ filing


@router.post("/api/tickets", status_code=201)
@limiter.limit("10/minute")
async def file_ticket(
    request: Request,
    body: TicketIn,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=100),
    actor: Actor | None = Depends(optional_actor),
    session: AsyncSession = Depends(get_session),
):
    cache_key = f"idem:{sha256_hex(idempotency_key)}"
    replay = await _cache_get(cache_key)
    if replay is not None:
        return JSONResponse(replay, status_code=200, headers={"Idempotent-Replay": "true"})

    existing = await find_by_idempotency_key(session, idempotency_key)
    if existing is not None:
        if actor is not None and is_petitioner(actor, existing):
            return JSONResponse(
                {"ticket": await ticket_out(session, existing), "anonymous_token": None}, status_code=200
            )
        raise HTTPException(409, "a ticket with this idempotency key already exists")

    uploads = await storage.take_finalized(body.upload_ids)
    try:
        ticket, raw_token = await create_ticket(
            session,
            actor=actor,
            title=body.title,
            description=body.description,
            category=body.category,
            priority=body.priority,
            anonymous=body.anonymous,
            idempotency_key=idempotency_key,
            uploads=uploads,
        )
        out = {"ticket": await ticket_out(session, ticket), "anonymous_token": raw_token}
        await events.commit(session)
    except IntegrityError:
        await events.rollback(session)
        raise HTTPException(409, "duplicate submission (idempotency key reused concurrently)") from None
    await _cache_set(cache_key, out)
    await storage.forget(body.upload_ids)
    return out


@router.get("/api/tickets/mine")
async def my_tickets(user: User = Depends(require_student), session: AsyncSession = Depends(get_session)):
    rows = (
        await session.scalars(select(Ticket).where(Ticket.petitioner_id == user.id).order_by(Ticket.created_at.desc()))
    ).all()
    return [ticket_summary(t) for t in rows]


@router.get("/api/anon/ticket")
async def anon_ticket(actor: Actor = Depends(require_actor), session: AsyncSession = Depends(get_session)):
    if actor.kind != "anon":
        raise HTTPException(400, "send the anonymous token in the X-Anon-Token header")
    ticket = await session.scalar(select(Ticket).where(Ticket.anon_token_hash == actor.anon_hash))
    if ticket is None:
        raise HTTPException(404, "no ticket for this token")
    return await ticket_out(session, ticket)


# ------------------------------------------------------------------ reading


@router.get("/api/tickets/{ticket_id}")
async def get_ticket(
    ticket_id: uuid.UUID, actor: Actor = Depends(require_actor), session: AsyncSession = Depends(get_session)
):
    ticket, staff = await load_visible_ticket(session, ticket_id, actor, "view")
    return await ticket_out(session, ticket, staff_view=staff)


@router.get("/api/tickets/{ticket_id}/ledger")
async def get_ledger(
    ticket_id: uuid.UUID, actor: Actor = Depends(require_actor), session: AsyncSession = Depends(get_session)
):
    ticket, _ = await load_visible_ticket(session, ticket_id, actor, "view_ledger")
    entries = (
        await session.scalars(select(LedgerEntry).where(LedgerEntry.ticket_id == ticket.id).order_by(LedgerEntry.seq))
    ).all()
    return {"ticket_id": str(ticket.id), "ref": ticket.ref, "entries": [entry_out(e) for e in entries]}


@router.get("/api/tickets/{ticket_id}/verify")
async def verify_ticket(
    ticket_id: uuid.UUID, actor: Actor = Depends(require_actor), session: AsyncSession = Depends(get_session)
):
    ticket, _ = await load_visible_ticket(session, ticket_id, actor, "verify")
    return (await ledger.verify_ticket(session, ticket)).to_dict()


@router.get("/api/tickets/{ticket_id}/access-log")
async def ticket_access_log(
    ticket_id: uuid.UUID, actor: Actor = Depends(require_actor), session: AsyncSession = Depends(get_session)
):
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None or not (is_petitioner(actor, ticket) or actor.is_admin):
        raise HTTPException(404, "ticket not found")
    rows = (
        await session.scalars(
            select(AccessLog).where(AccessLog.ticket_id == ticket.id).order_by(AccessLog.id.desc()).limit(200)
        )
    ).all()
    return [{"designation": r.designation, "action": r.action, "at": r.created_at.isoformat()} for r in rows]


@router.get("/api/tickets/{ticket_id}/attachments/{attachment_id}")
async def download_attachment(
    ticket_id: uuid.UUID,
    attachment_id: uuid.UUID,
    actor: Actor = Depends(require_actor),
    session: AsyncSession = Depends(get_session),
):
    ticket, _ = await load_visible_ticket(session, ticket_id, actor, "download")
    a = await session.scalar(
        select(Attachment).where(Attachment.id == attachment_id, Attachment.ticket_id == ticket.id)
    )
    if a is None:
        raise HTTPException(404)
    url = storage.backend().get_url(a.object_key, a.filename)
    if url:
        return RedirectResponse(url, status_code=307)
    data = storage.backend().read(a.object_key)
    return Response(
        data,
        media_type=a.content_type,
        headers={"Content-Disposition": f'attachment; filename="{a.filename}"', "X-Content-SHA256": a.sha256},
    )


# ------------------------------------------------------------------ acting


@router.post("/api/tickets/{ticket_id}/actions")
async def act(
    ticket_id: uuid.UUID,
    body: ActionIn,
    actor: Actor = Depends(require_actor),
    session: AsyncSession = Depends(get_session),
):
    try:
        ticket = await transition(session, ticket_id, body.action, actor, note=body.note)
        out = await ticket_out(session, ticket, staff_view=actor.user is not None and actor.user.role != "student")
        await events.commit(session)
    except TicketError:
        await events.rollback(session)
        raise
    return out


@router.post("/api/tickets/{ticket_id}/comments", status_code=201)
async def comment(
    ticket_id: uuid.UUID,
    body: CommentIn,
    actor: Actor = Depends(require_actor),
    session: AsyncSession = Depends(get_session),
):
    try:
        ticket = await add_comment(session, ticket_id, actor, body.text)
        await events.commit(session)
    except TicketError:
        await events.rollback(session)
        raise
    return {"ok": True, "head_seq": ticket.head_seq}
