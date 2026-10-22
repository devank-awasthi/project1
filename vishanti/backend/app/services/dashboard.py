"""Public transparency ledger.

Aggregates are computed by a background task on the read replica and cached
in Redis; the public endpoint only reads the cache, so public traffic never
touches the ticket write path.

Small-count suppression: any category/period cell with 1-4 cases is replaced
by null, so nobody can be identified from the numbers. If a row would then
have exactly one suppressed cell, the smallest other non-zero cell is also
suppressed, so the hidden value cannot be recovered by subtraction.
"""

from __future__ import annotations

import json
from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.canonical import utcnow
from app.config import settings
from app.enums import OPEN_STATUSES
from app.redis_client import get_redis

CACHE_KEY = "dashboard:public:v1"
IST = ZoneInfo("Asia/Kolkata")


def suppress(n: int | None) -> int | None:
    if n is None:
        return None
    return None if 0 < n < settings.SUPPRESSION_MIN else n


def suppress_row(values: list[int]) -> list[int | None]:
    out = [suppress(v) for v in values]
    hidden = [i for i, v in enumerate(values) if out[i] is None]
    if len(hidden) == 1:
        candidates = [i for i, v in enumerate(values) if out[i] is not None and v > 0]
        if candidates:
            out[min(candidates, key=lambda i: values[i])] = None
    return out


async def compute(session: AsyncSession) -> dict:
    now = utcnow()
    months = []
    cursor = now.astimezone(IST).replace(day=1)
    for _ in range(6):
        months.append(cursor.strftime("%Y-%m"))
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    months.reverse()

    categories = [
        r[0] for r in await session.execute(text("SELECT DISTINCT category FROM escalation_rules ORDER BY 1"))
    ]

    counts = {
        (r[0], r[1]): r[2]
        for r in await session.execute(
            text(
                """SELECT category, to_char(date_trunc('month', created_at AT TIME ZONE 'Asia/Kolkata'), 'YYYY-MM'), count(*)
                   FROM tickets WHERE created_at >= :since GROUP BY 1, 2"""
            ),
            {"since": now - timedelta(days=200)},
        )
    }
    heatmap = [{"category": c, "cells": suppress_row([counts.get((c, m), 0) for m in months])} for c in categories]

    velocity = []
    for r in await session.execute(
        text(
            """SELECT category, count(*) AS n,
                      percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM resolved_at - created_at) / 3600.0)
               FROM tickets WHERE resolved_at IS NOT NULL AND created_at >= :since GROUP BY 1"""
        ),
        {"since": now - timedelta(days=90)},
    ):
        ok = suppress(r[1]) is not None
        velocity.append(
            {"category": r[0], "resolved": suppress(r[1]), "median_hours": round(float(r[2]), 1) if ok else None}
        )

    compliance = []
    for r in await session.execute(
        text(
            """SELECT category, count(*) AS n, count(*) FILTER (WHERE breach_count = 0) AS on_time
               FROM tickets WHERE status IN ('RESOLVED', 'CLOSED') GROUP BY 1"""
        )
    ):
        ok = suppress(r[1]) is not None
        compliance.append(
            {"category": r[0], "finished": suppress(r[1]), "sla_met_pct": round(100.0 * r[2] / r[1], 1) if ok else None}
        )

    status_totals = {r[0]: r[1] for r in await session.execute(text("SELECT status, count(*) FROM tickets GROUP BY 1"))}
    open_by_level = {
        f"L{r[0]}": suppress(r[1])
        for r in await session.execute(
            text("SELECT current_level, count(*) FROM tickets WHERE status = ANY(:s) GROUP BY 1 ORDER BY 1"),
            {"s": list(OPEN_STATUSES)},
        )
    }

    flagged = [
        {"ref": r[0], "category": r[1], "since": r[2].isoformat()}
        for r in await session.execute(
            text(
                """SELECT ref, category, ombudsperson_alerted_at FROM tickets
                   WHERE ombudsperson_alerted_at IS NOT NULL AND status <> 'CLOSED'
                   ORDER BY ombudsperson_alerted_at DESC LIMIT 20"""
            )
        )
    ]

    systemic = [
        {"category": r[0], "size": r[1], "mood": round(float(r[2]), 2)}
        for r in await session.execute(
            text(
                """SELECT category, size, sentiment_sum / greatest(size, 1) FROM complaint_clusters
                   WHERE size >= :min ORDER BY size DESC LIMIT 10"""
            ),
            {"min": settings.SUPPRESSION_MIN},
        )
    ]

    checkpoints = [
        {"day": r[0].isoformat(), "root_hash": r[1], "tickets": r[2], "anchor_url": r[3]}
        for r in await session.execute(
            text("SELECT day, root_hash, ticket_count, anchor_url FROM checkpoints ORDER BY id DESC LIMIT 7")
        )
    ]

    return {
        "generated_at": now.isoformat(),
        "suppression_min": settings.SUPPRESSION_MIN,
        "months": months,
        "totals": {
            "filed": sum(status_totals.values()),
            "open": sum(v for k, v in status_totals.items() if k in OPEN_STATUSES),
            "resolved_or_closed": status_totals.get("RESOLVED", 0) + status_totals.get("CLOSED", 0),
        },
        "open_by_level": open_by_level,
        "heatmap": heatmap,
        "velocity": velocity,
        "sla_compliance": compliance,
        "ombudsperson_flags": flagged,
        "systemic_issues": systemic,
        "checkpoints": checkpoints,
    }


async def refresh(session: AsyncSession) -> dict:
    data = await compute(session)
    await get_redis().set(CACHE_KEY, json.dumps(data), ex=settings.DASHBOARD_CACHE_SECONDS)
    return data


async def cached(session_factory) -> dict:
    raw = await get_redis().get(CACHE_KEY)
    if raw:
        return json.loads(raw)
    async with session_factory() as session:  # cold cache: compute once on the read replica
        return await refresh(session)
