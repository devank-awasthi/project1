import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.ratelimit import limiter
from app.redis_client import get_redis
from app.routers import admin, auth, official, public, stream, tickets
from app.services.tickets import TicketError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("vishanti.http")

app = FastAPI(
    title="The Vishanti API",
    version="1.0.0",
    description="Tamper-evident grievance redressal: state machine, SHA-256 hash chains, SLA escalation.",
)
app.state.limiter = limiter

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Idempotent-Replay", "X-Content-SHA256"],
)


@app.middleware("http")
async def log_without_ip(request: Request, call_next):
    """Uvicorn's access log is disabled (see Dockerfile). This log line never
    contains a client address, and anonymous routes carry no identity."""
    start = time.perf_counter()
    response = await call_next(request)
    if not request.url.path.startswith("/api/stream"):
        log.info(
            "%s %s %s %.1fms",
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - start) * 1000,
        )
    return response


@app.exception_handler(TicketError)
async def ticket_error(_: Request, exc: TicketError):
    return JSONResponse({"detail": exc.message}, status_code=exc.status_code)


@app.exception_handler(RateLimitExceeded)
async def rate_limited(_: Request, exc: RateLimitExceeded):
    return JSONResponse({"detail": "too many requests, slow down"}, status_code=429)


for r in (auth.router, tickets.router, official.router, admin.router, public.router, stream.router):
    app.include_router(r)


@app.get("/api/health")
async def health():
    out = {"status": "ok"}
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        out["database"] = "ok"
    except Exception as exc:  # pragma: no cover
        out["database"] = f"down: {exc.__class__.__name__}"
        out["status"] = "degraded"
    try:
        await get_redis().ping()
        out["redis"] = "ok"
    except Exception as exc:
        out["redis"] = f"down: {exc.__class__.__name__}"
        out["status"] = "degraded"
    return out
