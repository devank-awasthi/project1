"""Rate limiting without IP logging.

The limiter key is HMAC(secret, day, client IP), so the counters stored in
Redis cannot be turned back into addresses, and they rotate daily.
"""

import hashlib
import hmac

from slowapi import Limiter
from starlette.requests import Request

from app.canonical import utcnow
from app.config import settings


def private_key(request: Request) -> str:
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
        request.client.host if request.client else "unknown"
    )
    day = utcnow().strftime("%Y-%m-%d")
    return hmac.new(settings.JWT_SECRET.encode(), f"{day}|{ip}".encode(), hashlib.sha256).hexdigest()[:32]


limiter = Limiter(
    key_func=private_key,
    storage_uri=settings.REDIS_URL,
    enabled=settings.ENABLE_RATE_LIMIT,
    headers_enabled=False,
)
