"""Create the schema, database-level guards and reference data.

python -m app.init_db            # schema + rules + demo accounts
python -m app.init_db --demo     # also files synthetic demo tickets
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app import models  # noqa: F401  (registers tables)
from app.catalog import DESIGNATION_LEVEL, ESCALATION_MATRIX, TRANSITIONS
from app.config import settings
from app.db import Base, make_engine
from app.enums import Role
from app.models import EscalationRule, TransitionRule, User
from app.schema_sql import POST_SQL, PRE_SQL
from app.security import hash_password

DEMO_PASSWORD = "vishanti123"


async def create_schema(eng: AsyncEngine) -> None:
    async with eng.begin() as conn:
        for stmt in PRE_SQL:
            await conn.execute(text(stmt))
        await conn.run_sync(Base.metadata.create_all)
        for stmt in POST_SQL:
            await conn.execute(text(stmt))


async def seed_reference(session: AsyncSession) -> None:
    if not await session.scalar(select(TransitionRule.id).limit(1)):
        for src, action, kind, dst, needs_assignee, desc in TRANSITIONS:
            session.add(
                TransitionRule(
                    from_state=src,
                    action=action,
                    actor_kind=kind,
                    to_state=dst,
                    requires_assignee=needs_assignee,
                    description=desc,
                )
            )
    if not await session.scalar(select(EscalationRule.id).limit(1)):
        for category, ladder in ESCALATION_MATRIX.items():
            for level, designation, hours in ladder:
                session.add(
                    EscalationRule(
                        category=category, level=level, handler_designation=designation, base_sla_hours=hours
                    )
                )
    await session.commit()


def _slug(s: str) -> str:
    return "".join(ch for ch in s.lower().replace(" ", ".") if ch.isalnum() or ch == ".")


async def seed_accounts(session: AsyncSession) -> None:
    if await session.scalar(select(User.id).limit(1)):
        return
    pw = hash_password(DEMO_PASSWORD)
    session.add(User(email="admin@vishanti.edu", password_hash=pw, full_name="System Administrator", role=Role.ADMIN))
    for designation, level in DESIGNATION_LEVEL.items():
        session.add(
            User(
                email=f"{_slug(designation)}@vishanti.edu",
                password_hash=pw,
                full_name=designation,
                role=Role.OFFICIAL,
                level=level,
                designation=designation,
            )
        )
    for i, name in enumerate(["Aarav Sharma", "Diya Verma", "Kabir Singh"], start=1):
        session.add(
            User(
                email=f"student{i}@vishanti.edu",
                password_hash=pw,
                full_name=name,
                role=Role.STUDENT,
                reg_no=f"2025{1000 + i}",
            )
        )
    await session.commit()


async def main(demo: bool) -> None:
    engine = make_engine(settings.MIGRATE_DATABASE_URL or settings.DATABASE_URL, nullpool=True)
    await create_schema(engine)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        await seed_reference(s)
        await seed_accounts(s)
    if demo:
        from app.demo_data import seed_demo_tickets

        await seed_demo_tickets(maker)
    await engine.dispose()
    print("database ready")


if __name__ == "__main__":
    asyncio.run(main("--demo" in sys.argv))
