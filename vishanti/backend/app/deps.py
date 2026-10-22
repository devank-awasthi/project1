"""Authentication and role guards as FastAPI dependencies, so no route
handler can forget a check."""

from __future__ import annotations

import uuid

import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actors import Actor
from app.db import get_session
from app.enums import Role
from app.models import User
from app.security import ANON_PREFIX, decode_token, hash_anonymous_token


async def _user_from_bearer(authorization: str | None, session: AsyncSession) -> User | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    try:
        claims = decode_token(authorization[7:].strip(), "access")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "access token expired") from None
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from None
    user = await session.scalar(select(User).where(User.id == uuid.UUID(claims["sub"])))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "account disabled")
    return user


async def optional_actor(
    authorization: str | None = Header(default=None),
    x_anon_token: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> Actor | None:
    if x_anon_token:
        if not x_anon_token.startswith(ANON_PREFIX):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "malformed anonymous token")
        return Actor(kind="anon", anon_hash=hash_anonymous_token(x_anon_token))
    user = await _user_from_bearer(authorization, session)
    return Actor(kind="user", user=user) if user else None


async def require_actor(actor: Actor | None = Depends(optional_actor)) -> Actor:
    if actor is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "login or anonymous token required")
    return actor


def require_role(*roles: Role):
    async def guard(actor: Actor | None = Depends(optional_actor)) -> User:
        if actor is None or actor.user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "login required")
        if actor.user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"requires role: {', '.join(roles)}")
        return actor.user

    return guard


require_student = require_role(Role.STUDENT)
require_official = require_role(Role.OFFICIAL)
require_admin = require_role(Role.ADMIN)
require_staff = require_role(Role.OFFICIAL, Role.ADMIN)
