"""Tests run against a real PostgreSQL (with pgvector) and Redis, because the
guarantees under test (row locks, SKIP LOCKED, append-only triggers, unique
constraints) are database behaviour that SQLite or mocks would not show.

    TEST_DATABASE_URL=postgresql+asyncpg://postgres@localhost:5432/vishanti_test
    TEST_REDIS_URL=redis://localhost:6379/15
"""

import os
import tempfile

os.environ["DATABASE_URL"] = os.getenv(
    "TEST_DATABASE_URL", "postgresql+asyncpg://postgres@localhost:5432/vishanti_test"
)
os.environ["READ_DATABASE_URL"] = os.environ["DATABASE_URL"]
os.environ["REDIS_URL"] = os.getenv("TEST_REDIS_URL", "redis://localhost:6379/15")
os.environ["EMBEDDINGS_BACKEND"] = os.getenv("TEST_EMBEDDINGS_BACKEND", "hashing")
os.environ["ENABLE_RATE_LIMIT"] = "false"
os.environ["STORAGE_BACKEND"] = "local"
os.environ["LOCAL_STORAGE_DIR"] = tempfile.mkdtemp(prefix="vishanti-test-")
os.environ["BCRYPT_ROUNDS"] = "4"
os.environ["SLA_HOUR_SECONDS"] = "3600"

import uuid  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from app.actors import Actor  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.enums import Role  # noqa: E402
from app.init_db import create_schema, seed_accounts, seed_reference  # noqa: E402
from app.models import User  # noqa: E402
from app.redis_client import get_redis  # noqa: E402
from app.services import events  # noqa: E402
from app.services.tickets import create_ticket  # noqa: E402


@pytest.fixture(autouse=True)
async def fresh_db():
    """Every test starts from an empty schema plus reference data."""
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await create_schema(engine)
    async with SessionLocal() as s:
        await seed_reference(s)
        await seed_accounts(s)
    await get_redis().flushdb()
    events.task_sink = []
    yield
    events.task_sink = None


@pytest.fixture
def queued_tasks():
    return events.task_sink


async def user(email: str) -> User:
    async with SessionLocal() as s:
        return await s.scalar(select(User).where(User.email == email))


async def as_actor(email: str) -> Actor:
    return Actor(kind="user", user=await user(email))


async def make_official(designation: str, level: int, email: str) -> User:
    async with SessionLocal() as s:
        u = User(
            email=email, password_hash="x", full_name=email, role=Role.OFFICIAL, designation=designation, level=level
        )
        s.add(u)
        await s.commit()
        return u


async def file(
    category: str = "Hostel",
    priority: str = "medium",
    anonymous: bool = False,
    by: str = "student1@vishanti.edu",
    title: str = "Water leakage in hostel room",
    description: str = "Water is leaking from the ceiling of room 214 since Monday.",
    now=None,
):
    actor = await as_actor(by)
    async with SessionLocal() as s:
        ticket, token = await create_ticket(
            s,
            actor=actor,
            title=title,
            description=description,
            category=category,
            priority=priority,
            anonymous=anonymous,
            idempotency_key=f"test-{uuid.uuid4()}",
            now=now,
        )
        await events.commit(s)
        return ticket, token


async def superuser_sql(*statements: str, params: dict | None = None) -> None:
    """Simulates an insider with database superuser rights: switches the
    append-only triggers off, edits rows, switches them back on."""
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE ledger_entries DISABLE TRIGGER USER"))
        for st in statements:
            await conn.execute(text(st), params or {})
        await conn.execute(text("ALTER TABLE ledger_entries ENABLE TRIGGER USER"))
