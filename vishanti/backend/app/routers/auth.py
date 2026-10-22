import uuid

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actors import Actor
from app.db import get_session
from app.deps import require_actor
from app.enums import Role
from app.models import User
from app.ratelimit import limiter
from app.schemas import LoginIn, RefreshIn, RegisterIn
from app.security import decode_token, hash_password, issue_tokens, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _user_out(u: User) -> dict:
    return {
        "id": str(u.id),
        "email": u.email,
        "full_name": u.full_name,
        "role": u.role,
        "level": u.level,
        "designation": u.designation,
        "reg_no": u.reg_no,
    }


@router.post("/register", status_code=201)
@limiter.limit("5/minute")
async def register(request: Request, body: RegisterIn, session: AsyncSession = Depends(get_session)):
    if await session.scalar(select(User.id).where(User.email == body.email.lower())):
        raise HTTPException(409, "an account with this email already exists")
    user = User(
        email=body.email.lower(),
        password_hash=hash_password(body.password),
        full_name=body.full_name,
        role=Role.STUDENT,
        reg_no=body.reg_no,
    )
    session.add(user)
    await session.commit()
    return {"user": _user_out(user), **issue_tokens(user.id, user.role, user.level, user.designation)}


@router.post("/login")
@limiter.limit("10/minute")
async def login(request: Request, body: LoginIn, session: AsyncSession = Depends(get_session)):
    user = await session.scalar(select(User).where(User.email == body.email.lower()))
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "invalid email or password")
    return {"user": _user_out(user), **issue_tokens(user.id, user.role, user.level, user.designation)}


@router.post("/refresh")
async def refresh(body: RefreshIn, session: AsyncSession = Depends(get_session)):
    try:
        claims = decode_token(body.refresh_token, "refresh")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "invalid refresh token") from None
    user = await session.get(User, uuid.UUID(claims["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(401, "account disabled")
    return {"user": _user_out(user), **issue_tokens(user.id, user.role, user.level, user.designation)}


@router.get("/me")
async def me(actor: Actor = Depends(require_actor)):
    if actor.kind == "anon":
        return {"anonymous": True}
    return {"user": _user_out(actor.user)}
