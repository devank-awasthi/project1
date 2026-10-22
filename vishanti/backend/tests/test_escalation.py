"""SLA engine: timers, auto-escalation, idempotency, the ombudsperson, auto-close,
warnings. Time is passed in explicitly instead of waiting."""

import asyncio
from datetime import timedelta

from sqlalchemy import select

from app.canonical import format_ts, utcnow
from app.db import SessionLocal
from app.enums import Action, TicketStatus
from app.models import EscalationRule, LedgerEntry, Notification, Ticket
from app.services import escalation, events, ledger
from app.services.tickets import transition
from tests.conftest import as_actor, file


async def get(tid) -> Ticket:
    async with SessionLocal() as s:
        return await s.get(Ticket, tid)


async def escalate(tid, now, expected=None):
    async with SessionLocal() as s:
        out = await escalation.escalate_if_due(s, tid, expected, now=now)
        await events.commit(s)
        return out


async def test_new_ticket_queues_timer_task_at_due_time(queued_tasks):
    t, _ = await file()
    timer = [q for q in queued_tasks if q["name"] == "vishanti.escalate_ticket"]
    assert len(timer) == 1
    assert timer[0]["eta"] == t.due_at
    assert timer[0]["args"] == [str(t.id), format_ts(t.due_at)]
    assert any(q["name"] == "vishanti.embed_ticket" for q in queued_tasks)


async def test_not_due_and_stale_tasks_do_nothing():
    t, _ = await file()
    assert await escalate(t.id, now=t.due_at - timedelta(seconds=1)) == "not_due"
    assert await escalate(t.id, now=t.due_at + timedelta(seconds=1), expected="1999-01-01T00:00:00.000000Z") == "stale"
    assert (await get(t.id)).current_level == 1


async def test_unassigned_ticket_still_escalates_and_is_routed():
    """The clock starts at Filed, so a ticket nobody touched still moves."""
    t, _ = await file(category="Hostel")
    out = await escalate(t.id, now=t.due_at + timedelta(minutes=1), expected=format_ts(t.due_at))
    after = await get(t.id)
    assert out == "escalated"
    assert after.current_level == 2 and after.status == TicketStatus.ASSIGNED
    assert after.breach_count == 1
    assert after.due_at > t.due_at  # fresh timer for the Dean
    async with SessionLocal() as s:
        actions = [
            e.action
            for e in (
                await s.scalars(select(LedgerEntry).where(LedgerEntry.ticket_id == t.id).order_by(LedgerEntry.seq))
            ).all()
        ]
    assert actions == ["GENESIS", "SLA_ESCALATE", "AUTO_ASSIGN"]


async def test_duplicate_delivery_escalates_once():
    t, _ = await file()
    later = t.due_at + timedelta(minutes=1)
    expected = format_ts(t.due_at)
    assert await escalate(t.id, later, expected) == "escalated"
    assert await escalate(t.id, later, expected) == "stale"  # same task delivered again
    assert await escalate(t.id, later) == "not_due"  # sweeper right after
    assert (await get(t.id)).current_level == 2


async def test_parallel_workers_escalate_once():
    t, _ = await file()
    later = t.due_at + timedelta(minutes=1)
    results = await asyncio.gather(*[escalate(t.id, later, format_ts(t.due_at)) for _ in range(6)])
    assert sorted(results).count("escalated") == 1
    after = await get(t.id)
    assert after.current_level == 2 and after.breach_count == 1


async def test_full_ladder_then_ombudsperson():
    t, _ = await file(category="IT & Network")
    now = utcnow()
    outcomes = []
    for _ in range(4):
        cur = await get(t.id)
        now = cur.due_at + timedelta(minutes=1)
        outcomes.append(await escalate(t.id, now))
    assert outcomes == ["escalated", "escalated", "ombudsperson", "not_open"]
    final = await get(t.id)
    assert final.current_level == 3 and final.ombudsperson_alerted_at is not None
    async with SessionLocal() as s:
        note = await s.scalar(select(Notification).where(Notification.event_type == "ombudsperson_alert"))
        assert note is not None
        assert (await ledger.verify_ticket(s, final)).ok


async def test_sweeper_backstop_and_second_run_is_noop():
    a, _ = await file()
    b, _ = await file(category="Mess")
    c, _ = await file(category="Academics")  # not overdue at the chosen time
    when = max(a.due_at, b.due_at) + timedelta(minutes=1)
    assert when < c.due_at
    first = await escalation.sweep(SessionLocal, now=when)
    second = await escalation.sweep(SessionLocal, now=when)
    assert first["escalated"] == 2 and second["escalated"] == 0
    assert (await get(c.id)).current_level == 1


async def test_parallel_sweepers_do_not_double_escalate():
    tickets = [await file() for _ in range(8)]
    when = max(t.due_at for t, _ in tickets) + timedelta(minutes=1)
    runs = await asyncio.gather(*[escalation.sweep(SessionLocal, now=when) for _ in range(3)])
    assert sum(r["escalated"] for r in runs) == 8
    for t, _ in tickets:
        assert (await get(t.id)).breach_count == 1


async def test_resolved_ticket_auto_closes_after_confirmation_window():
    t, _ = await file()
    w = await as_actor("warden@vishanti.edu")
    for action, note in ((Action.ASSIGN, None), (Action.START_REVIEW, None), (Action.RESOLVE, "fixed")):
        async with SessionLocal() as s:
            await transition(s, t.id, action, w, note=note)
            await events.commit(s)
    resolved = await get(t.id)
    assert resolved.confirm_by is not None
    counts = await escalation.sweep(SessionLocal, now=resolved.confirm_by - timedelta(minutes=1))
    assert counts["auto_closed"] == 0
    counts = await escalation.sweep(SessionLocal, now=resolved.confirm_by + timedelta(minutes=1))
    assert counts["auto_closed"] == 1
    assert (await get(t.id)).status == TicketStatus.CLOSED


async def test_resolved_ticket_is_not_escalated():
    """Marking Resolved pauses the SLA; it cannot be gamed into closing early
    because only the petitioner (or the 72h window) closes it."""
    t, _ = await file()
    w = await as_actor("warden@vishanti.edu")
    for action, note in ((Action.ASSIGN, None), (Action.START_REVIEW, None), (Action.RESOLVE, "fixed")):
        async with SessionLocal() as s:
            await transition(s, t.id, action, w, note=note)
            await events.commit(s)
    assert await escalate(t.id, t.due_at + timedelta(hours=1)) == "not_open"


async def test_sla_warning_sent_once_per_deadline():
    t, _ = await file()  # 48h timer, warn when <= 20% (9.6h) left
    early = t.due_at - timedelta(hours=20)
    late = t.due_at - timedelta(hours=5)
    assert await escalation.warn_upcoming(SessionLocal, now=early) == 0
    assert await escalation.warn_upcoming(SessionLocal, now=late) == 1
    assert await escalation.warn_upcoming(SessionLocal, now=late) == 0
    async with SessionLocal() as s:
        n = await s.scalar(select(Notification).where(Notification.event_type == "sla_warning"))
    assert "SLA breach in 300 minutes" in n.payload["message"]


async def test_rule_change_applies_to_new_tickets_without_redeploy():
    async with SessionLocal() as s:
        rule = await s.scalar(
            select(EscalationRule).where(EscalationRule.category == "Hostel", EscalationRule.level == 1)
        )
        rule.base_sla_hours = 4
        await s.commit()
    t, _ = await file(priority="medium")
    assert round((t.due_at - t.created_at).total_seconds() / 3600) == 4


async def test_celery_task_code_path_end_to_end():
    """Runs the real Celery task body (asyncio.run + fresh engine) in a thread."""
    from app import worker

    t, _ = await file()
    async with SessionLocal() as s:
        tk = await s.get(Ticket, t.id)
        tk.due_at = utcnow() - timedelta(seconds=5)  # make it overdue now
        await s.commit()
        due = format_ts(tk.due_at)
    out = await asyncio.to_thread(worker.escalate_ticket.run, str(t.id), due)
    assert out == "escalated"
    again = await asyncio.to_thread(worker.escalate_ticket.run, str(t.id), due)
    assert again == "stale"
