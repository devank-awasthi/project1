"""Celery tasks. Each task runs the same async service code the API uses,
on a fresh event loop with a non-pooled engine (asyncpg connections cannot
cross event loops)."""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.celery_app import celery_app
from app.config import settings
from app.db import make_engine
from app.redis_client import close_loop_redis
from app.services import dashboard, escalation, events, ledger
from app.services.embeddings import embed_and_cluster

log = logging.getLogger("vishanti.worker")


def _run(fn):
    async def runner():
        eng = make_engine(settings.DATABASE_URL, nullpool=True)
        maker = async_sessionmaker(eng, expire_on_commit=False)
        try:
            return await fn(maker)
        finally:
            await eng.dispose()
            await close_loop_redis()

    return asyncio.run(runner())


async def _in_txn(maker, coro_fn):
    async with maker() as session:
        try:
            result = await coro_fn(session)
            await events.commit(session)
            return result
        except Exception:
            await events.rollback(session)
            raise


@celery_app.task(name="vishanti.escalate_ticket", bind=True, max_retries=5, default_retry_delay=10)
def escalate_ticket(self, ticket_id: str, expected_due: str):
    try:
        outcome = _run(
            lambda m: _in_txn(m, lambda s: escalation.escalate_if_due(s, uuid.UUID(ticket_id), expected_due))
        )
    except Exception as exc:
        raise self.retry(exc=exc) from exc
    log.info("escalate_ticket %s -> %s", ticket_id, outcome)
    return outcome


@celery_app.task(name="vishanti.auto_close_ticket")
def auto_close_ticket(ticket_id: str):
    return _run(lambda m: _in_txn(m, lambda s: escalation.auto_close_if_due(s, uuid.UUID(ticket_id))))


@celery_app.task(name="vishanti.sweep")
def sweep():
    counts = _run(lambda m: escalation.sweep(m))
    if any(counts.values()):
        log.info("sweeper: %s", counts)
    return counts


@celery_app.task(name="vishanti.warn_sla")
def warn_sla():
    return _run(lambda m: escalation.warn_upcoming(m))


@celery_app.task(name="vishanti.embed_ticket", bind=True, max_retries=3, default_retry_delay=30)
def embed_ticket(self, ticket_id: str):
    try:
        return _run(lambda m: _in_txn(m, lambda s: embed_and_cluster(s, uuid.UUID(ticket_id))))
    except Exception as exc:
        raise self.retry(exc=exc) from exc


@celery_app.task(name="vishanti.verify_chains")
def verify_chains():
    run = _run(lambda m: _in_txn(m, lambda s: ledger.verify_all(s, "scheduler")))
    return {"tickets": run.tickets_checked, "failures": run.failures, "checkpoints_ok": run.checkpoints_ok}


@celery_app.task(name="vishanti.daily_checkpoint")
def daily_checkpoint():
    cp = _run(lambda m: _in_txn(m, lambda s: ledger.create_checkpoint(s)))
    return {"root_hash": cp.root_hash, "anchor_url": cp.anchor_url}


@celery_app.task(name="vishanti.refresh_dashboard")
def refresh_dashboard():
    async def go(maker):
        eng = make_engine(settings.READ_DATABASE_URL or settings.DATABASE_URL, nullpool=True)
        try:
            async with async_sessionmaker(eng)() as session:
                await dashboard.refresh(session)
        finally:
            await eng.dispose()
        return True

    return _run(go)
