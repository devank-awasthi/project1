"""Row locking: concurrent writers on one ticket are serialised, and the
chain never forks."""

import asyncio

from sqlalchemy import func, select

from app.actors import Actor
from app.db import SessionLocal
from app.enums import Action, TicketStatus
from app.models import LedgerEntry, Ticket
from app.services import events, ledger
from app.services.tickets import TicketError, add_comment, transition
from tests.conftest import as_actor, file, make_official


async def attempt(fn):
    async with SessionLocal() as s:
        try:
            await fn(s)
            await events.commit(s)
            return "ok"
        except TicketError as e:
            await events.rollback(s)
            return e.status_code


async def test_two_officials_pick_up_the_same_ticket_one_wins():
    t, _ = await file()
    officials = [await as_actor("warden@vishanti.edu")] + [
        Actor(kind="user", user=await make_official("Warden", 1, f"warden{i}@vishanti.edu")) for i in range(2, 6)
    ]
    results = await asyncio.gather(*[attempt(lambda s, o=o: transition(s, t.id, Action.ASSIGN, o)) for o in officials])
    assert results.count("ok") == 1
    assert results.count(409) == len(officials) - 1  # the others see ASSIGNED and are refused
    async with SessionLocal() as s:
        n = await s.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.ticket_id == t.id))
        assert n == 2
        assert (await s.get(Ticket, t.id)).status == TicketStatus.ASSIGNED


async def test_parallel_comments_never_fork_the_chain():
    t, _ = await file()
    w = await as_actor("warden@vishanti.edu")
    student = await as_actor("student1@vishanti.edu")
    jobs = [attempt(lambda s, i=i: add_comment(s, t.id, w if i % 2 else student, f"note {i}")) for i in range(20)]
    assert (await asyncio.gather(*jobs)).count("ok") == 20
    async with SessionLocal() as s:
        seqs = (
            await s.scalars(select(LedgerEntry.seq).where(LedgerEntry.ticket_id == t.id).order_by(LedgerEntry.seq))
        ).all()
        assert seqs == list(range(21))
        assert (await ledger.verify_ticket(s, await s.get(Ticket, t.id))).ok


async def test_resolve_and_escalate_race():
    """The assignee resolves at the same moment the SLA worker fires.
    Whichever takes the lock first wins; the other sees the new state."""
    from datetime import timedelta

    from app.services import escalation

    t, _ = await file()
    w = await as_actor("warden@vishanti.edu")
    for a in (Action.ASSIGN, Action.START_REVIEW):
        assert await attempt(lambda s, a=a: transition(s, t.id, a, w)) == "ok"
    late = t.due_at + timedelta(seconds=1)

    async def worker(s):
        return await escalation.escalate_if_due(s, t.id, now=late)

    res = await asyncio.gather(attempt(lambda s: transition(s, t.id, Action.RESOLVE, w, note="done")), attempt(worker))
    async with SessionLocal() as s:
        final = await s.get(Ticket, t.id)
        assert (await ledger.verify_ticket(s, final)).ok
    # Either resolved at level 1, or escalated (then the warden's resolve is refused).
    assert (final.status == TicketStatus.RESOLVED and final.current_level == 1) or (
        final.current_level == 2 and res[0] in (403, 409)
    )


async def test_tickets_do_not_block_each_other():
    tickets = [(await file())[0] for _ in range(10)]
    w = await as_actor("warden@vishanti.edu")
    res = await asyncio.gather(*[attempt(lambda s, t=t: transition(s, t.id, Action.ASSIGN, w)) for t in tickets])
    assert res == ["ok"] * 10
