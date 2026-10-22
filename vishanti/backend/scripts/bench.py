"""Reproducible numbers for the README.

    BENCH_DATABASE_URL=postgresql+asyncpg://postgres@localhost:5432/vishanti_bench \\
        python -m scripts.bench --tickets 2000

Uses its own database (wiped). Measures, through the real service code:
  1. filing throughput (create_ticket + GENESIS + notifications, committed)
  2. SLA sweep: N overdue tickets escalated by 3 parallel sweepers, checking
     that every ticket was escalated exactly once
  3. full-ledger verification time
  4. HTTP filing latency (p50/p95) against a running API, if --api is given
"""

import argparse
import asyncio
import os
import statistics
import time
import uuid
from datetime import timedelta

os.environ.setdefault("EMBEDDINGS_BACKEND", "hashing")
os.environ["DATABASE_URL"] = os.getenv("BENCH_DATABASE_URL", "postgresql+asyncpg://postgres@localhost:5432/vishanti_bench")
os.environ["BCRYPT_ROUNDS"] = "4"

from sqlalchemy import func, select, text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker  # noqa: E402

from app.actors import Actor  # noqa: E402
from app.db import make_engine  # noqa: E402
from app.init_db import create_schema, seed_accounts, seed_reference  # noqa: E402
from app.models import Ticket, User  # noqa: E402
from app.services import escalation, events, ledger  # noqa: E402
from app.services.tickets import create_ticket  # noqa: E402

CATS = ["Hostel", "Mess", "Academics", "Fees & Finance", "IT & Network", "Library"]


async def main(n: int, api: str | None) -> None:
    engine = make_engine(os.environ["DATABASE_URL"], nullpool=False)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await create_schema(engine)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        await seed_reference(s)
        await seed_accounts(s)
        student = await s.scalar(select(User).where(User.email == "student1@vishanti.edu"))
    events.task_sink = []
    actor = Actor(kind="user", user=student)

    async def file_one(i: int):
        async with maker() as s:
            await create_ticket(
                s,
                actor=actor,
                title=f"Benchmark complaint {i}",
                description="Synthetic benchmark complaint used to measure throughput.",
                category=CATS[i % len(CATS)],
                priority="medium",
                anonymous=i % 5 == 0,
                idempotency_key=f"bench-{uuid.uuid4()}",
            )
            await events.commit(s)

    sem = asyncio.Semaphore(16)

    async def guarded(i):
        async with sem:
            await file_one(i)

    t0 = time.perf_counter()
    await asyncio.gather(*[guarded(i) for i in range(n)])
    dt = time.perf_counter() - t0
    print(f"filing: {n} tickets in {dt:.2f}s = {n / dt:.0f} tickets/s (16 concurrent writers)")

    async with maker() as s:
        latest_due = await s.scalar(select(func.max(Ticket.due_at)))
    when = latest_due + timedelta(minutes=1)
    t0 = time.perf_counter()
    runs = await asyncio.gather(*[escalation.sweep(maker, now=when, limit=n) for _ in range(3)])
    dt = time.perf_counter() - t0
    total = sum(r["escalated"] for r in runs)
    async with maker() as s:
        wrong = await s.scalar(select(func.count()).select_from(Ticket).where(Ticket.breach_count != 1))
    print(
        f"sweep: {total} overdue tickets escalated by 3 parallel sweepers in {dt:.2f}s "
        f"= {total / dt:.0f}/s; per sweeper {[r['escalated'] for r in runs]}; tickets not escalated exactly once: {wrong}"
    )

    async with maker() as s:
        t0 = time.perf_counter()
        run = await ledger.verify_all(s, "bench")
        dt = time.perf_counter() - t0
        await s.commit()
    print(
        f"verify: {run.tickets_checked} chains, {run.entries_checked} entries in {dt:.2f}s "
        f"= {run.entries_checked / dt:.0f} entries/s, failures {run.failures}"
    )

    if api:
        import httpx

        lat = []
        async with httpx.AsyncClient(base_url=api, timeout=30) as c:
            tok = (await c.post("/api/auth/login", json={"email": "student1@vishanti.edu", "password": "vishanti123"})).json()
            h = {"Authorization": f"Bearer {tok['access_token']}"}

            async def one(i):
                async with sem:
                    t = time.perf_counter()
                    r = await c.post(
                        "/api/tickets",
                        headers={**h, "Idempotency-Key": str(uuid.uuid4())},
                        json={
                            "title": f"HTTP bench {i}",
                            "description": "Synthetic complaint for HTTP latency.",
                            "category": "Hostel",
                            "priority": "low",
                        },
                    )
                    assert r.status_code == 201, r.text
                    lat.append((time.perf_counter() - t) * 1000)

            await asyncio.gather(*[one(i) for i in range(300)])
        lat.sort()
        print(
            f"http filing: 300 requests, 16 concurrent: p50 {statistics.median(lat):.0f} ms, "
            f"p95 {lat[int(len(lat) * 0.95)]:.0f} ms, max {lat[-1]:.0f} ms"
        )
    await engine.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickets", type=int, default=2000)
    ap.add_argument("--api", default=None, help="base URL of an API started on the bench database")
    a = ap.parse_args()
    asyncio.run(main(a.tickets, a.api))
