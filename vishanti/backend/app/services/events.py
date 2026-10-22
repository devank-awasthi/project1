"""Side effects that must only happen after the database commit succeeds.

A service function never publishes to Redis or queues a Celery task directly.
It records the intent on the session; `commit()` performs them once the
transaction is durable. If the commit fails, nothing leaks out. If Redis is
down after the commit, the ticket is still safe: notifications are stored in
Postgres (replayed via Last-Event-ID) and the sweeper escalates from `due_at`.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.redis_client import get_redis

log = logging.getLogger("vishanti.events")
_KEY = "vishanti_post_commit"

# Tests replace this to capture queued tasks instead of sending them.
task_sink: list | None = None


def after_commit(session: AsyncSession, kind: str, **data: Any) -> None:
    session.info.setdefault(_KEY, []).append((kind, data))


def queue_task(session: AsyncSession, name: str, args: list, eta: datetime | None = None) -> None:
    after_commit(session, "task", name=name, args=args, eta=eta)


async def commit(session: AsyncSession) -> None:
    await session.commit()
    events = session.info.pop(_KEY, [])
    await dispatch(events)


async def rollback(session: AsyncSession) -> None:
    session.info.pop(_KEY, None)
    await session.rollback()


def _send_task(name: str, args: list, eta: datetime | None) -> None:
    from app.celery_app import celery_app

    celery_app.send_task(name, args=args, eta=eta)


async def dispatch(events: list[tuple[str, dict]]) -> None:
    for kind, data in events:
        try:
            if kind == "publish":
                await get_redis().publish(f"vishanti:{data['channel']}", json.dumps(data["message"], default=str))
            elif kind == "task":
                if task_sink is not None:
                    task_sink.append(data)
                else:
                    await run_in_threadpool(_send_task, data["name"], data["args"], data.get("eta"))
        except Exception as exc:  # degraded mode, see module docstring
            log.warning("post-commit %s failed (%s); durable state is already committed", kind, exc)
