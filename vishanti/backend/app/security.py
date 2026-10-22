import secrets
import uuid
from datetime import timedelta

import jwt
from passlib.context import CryptContext

from app.canonical import sha256_hex, utcnow
from app.config import settings

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=settings.BCRYPT_ROUNDS)


def hash_password(raw: str) -> str:
    return _pwd.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    return _pwd.verify(raw, hashed)


def _encode(claims: dict, ttl: timedelta) -> str:
    now = utcnow()
    body = {**claims, "iat": int(now.timestamp()), "exp": int((now + ttl).timestamp())}
    return jwt.encode(body, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def issue_tokens(user_id: uuid.UUID, role: str, level: int, designation: str | None) -> dict:
    claims = {"sub": str(user_id), "role": role, "level": level, "designation": designation}
    return {
        "access_token": _encode({**claims, "typ": "access"}, timedelta(minutes=settings.ACCESS_TTL_MINUTES)),
        "refresh_token": _encode({"sub": str(user_id), "typ": "refresh"}, timedelta(days=settings.REFRESH_TTL_DAYS)),
        "token_type": "bearer",
    }


def decode_token(token: str, expected_type: str) -> dict:
    claims = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    if claims.get("typ") != expected_type:
        raise jwt.InvalidTokenError("wrong token type")
    return claims


# --- Anonymous petitioner tokens -------------------------------------------------
# 32 random bytes = 256 bits. Shown to the petitioner exactly once; the server
# keeps only SHA-256(token), so a database leak does not reveal usable tokens.

ANON_PREFIX = "vsh_"


def new_anonymous_token() -> tuple[str, str]:
    raw = ANON_PREFIX + secrets.token_urlsafe(32)
    return raw, hash_anonymous_token(raw)


def hash_anonymous_token(raw: str) -> str:
    return sha256_hex(raw.strip())
