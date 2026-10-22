import pytest
from sqlalchemy import select

from app.actors import Actor
from app.db import SessionLocal
from app.enums import Action, TicketStatus
from app.models import LedgerEntry, Ticket
from app.services import events, ledger
from app.services.tickets import TicketError, transition
from tests.conftest import as_actor, file, user

WARDEN = "warden@vishanti.edu"
DEAN = "dean.of.students@vishanti.edu"
REGISTRAR = "registrar@vishanti.edu"
BURSAR = "bursar@vishanti.edu"


async def act(ticket_id, action, actor, note=None):
    async with SessionLocal() as s:
        try:
            t = await transition(s, ticket_id, action, actor, note=note)
            await events.commit(s)
            return t
        except Exception:
            await events.rollback(s)
            raise


async def chain(ticket_id):
    async with SessionLocal() as s:
        return (
            await s.scalars(select(LedgerEntry).where(LedgerEntry.ticket_id == ticket_id).order_by(LedgerEntry.seq))
        ).all()


async def verify(ticket_id):
    async with SessionLocal() as s:
        return await ledger.verify_ticket(s, await s.get(Ticket, ticket_id))


async def test_happy_path_filed_to_closed():
    t, _ = await file()
    warden = await as_actor(WARDEN)
    student = await as_actor("student1@vishanti.edu")
    assert t.status == TicketStatus.FILED and t.due_at is not None  # clock starts at Filed

    await act(t.id, Action.ASSIGN, warden)
    await act(t.id, Action.START_REVIEW, warden)
    await act(t.id, Action.RESOLVE, warden, note="Plumber fixed the pipe")
    final = await act(t.id, Action.CONFIRM, student)

    assert final.status == TicketStatus.CLOSED
    entries = await chain(t.id)
    assert [e.action for e in entries] == ["GENESIS", "ASSIGN", "START_REVIEW", "RESOLVE", "CONFIRM"]
    assert [e.to_status for e in entries] == ["FILED", "ASSIGNED", "IN_REVIEW", "RESOLVED", "CLOSED"]
    assert (await verify(t.id)).ok


@pytest.mark.parametrize(
    "action",
    [Action.START_REVIEW, Action.RESOLVE, Action.CONFIRM, Action.DISPUTE],
)
async def test_states_cannot_be_skipped_from_filed(action):
    t, _ = await file()
    actor = await as_actor(WARDEN if action in (Action.START_REVIEW, Action.RESOLVE) else "student1@vishanti.edu")
    with pytest.raises(TicketError) as e:
        await act(t.id, action, actor, note="x")
    assert e.value.status_code == 409
    assert len(await chain(t.id)) == 1  # nothing appended


async def test_wrong_designation_cannot_act():
    t, _ = await file(category="Hostel")
    with pytest.raises(TicketError) as e:
        await act(t.id, Action.ASSIGN, await as_actor(BURSAR))
    assert e.value.status_code == 403


async def test_higher_level_cannot_jump_the_queue():
    """The Dean is level 2; a fresh Hostel ticket is level 1."""
    t, _ = await file(category="Hostel")
    with pytest.raises(TicketError) as e:
        await act(t.id, Action.ASSIGN, await as_actor(DEAN))
    assert e.value.status_code == 403


async def test_only_assignee_can_review_and_resolve():
    from tests.conftest import make_official

    t, _ = await file()
    await act(t.id, Action.ASSIGN, await as_actor(WARDEN))
    other = await make_official("Warden", 1, "warden2@vishanti.edu")
    with pytest.raises(TicketError) as e:
        await act(t.id, Action.START_REVIEW, Actor(kind="user", user=other))
    assert e.value.status_code == 403


async def test_stranger_student_is_not_a_party():
    t, _ = await file(by="student1@vishanti.edu")
    with pytest.raises(TicketError) as e:
        await act(t.id, Action.CONFIRM, await as_actor("student2@vishanti.edu"))
    assert e.value.status_code == 403


async def test_petitioner_cannot_assign():
    t, _ = await file()
    with pytest.raises(TicketError) as e:
        await act(t.id, Action.ASSIGN, await as_actor("student1@vishanti.edu"))
    assert e.value.status_code == 409  # no rule for petitioner + ASSIGN


async def test_resolution_needs_a_note():
    t, _ = await file()
    w = await as_actor(WARDEN)
    await act(t.id, Action.ASSIGN, w)
    await act(t.id, Action.START_REVIEW, w)
    with pytest.raises(TicketError) as e:
        await act(t.id, Action.RESOLVE, w, note=None)
    assert e.value.status_code == 422


async def test_dispute_reopens_one_level_up_and_routes_to_dean():
    t, _ = await file()
    w = await as_actor(WARDEN)
    await act(t.id, Action.ASSIGN, w)
    await act(t.id, Action.START_REVIEW, w)
    await act(t.id, Action.RESOLVE, w, note="done")
    old_due = (await chain(t.id))[-1]
    after = await act(t.id, Action.DISPUTE, await as_actor("student1@vishanti.edu"), note="still leaking")

    dean = await user(DEAN)
    assert after.current_level == 2
    assert after.status == TicketStatus.ASSIGNED  # auto-assigned at the new level
    assert after.assignee_id == dean.id
    entries = await chain(t.id)
    assert [e.action for e in entries[-2:]] == ["DISPUTE", "AUTO_ASSIGN"]
    assert entries[-1].actor == "system:router"
    assert old_due is not None and (await verify(t.id)).ok


async def test_dispute_at_top_level_alerts_ombudsperson():
    t, _ = await file(category="Mess")
    ladder = ["mess.manager@vishanti.edu", DEAN, REGISTRAR]
    student = await as_actor("student1@vishanti.edu")
    for i, email in enumerate(ladder):
        off = await as_actor(email)
        async with SessionLocal() as s:
            status = (await s.get(Ticket, t.id)).status
        if status in (TicketStatus.FILED, TicketStatus.ESCALATED):
            await act(t.id, Action.ASSIGN, off)
        await act(t.id, Action.START_REVIEW, off)
        await act(t.id, Action.RESOLVE, off, note=f"fixed at level {i + 1}")
        await act(t.id, Action.DISPUTE, student, note="not fixed")

    async with SessionLocal() as s:
        final = await s.get(Ticket, t.id)
    assert final.current_level == 3
    assert final.ombudsperson_alerted_at is not None
    actions = [e.action for e in await chain(t.id)]
    assert "OMBUDSPERSON_ALERT" in actions
    assert (await verify(t.id)).ok


async def test_anonymous_ticket_has_no_identity_anywhere():
    student = await user("student1@vishanti.edu")
    t, token = await file(anonymous=True)  # filed while logged in
    assert token and token.startswith("vsh_") and len(token) >= 40
    assert t.petitioner_id is None
    assert t.anon_token_hash and t.anon_token_hash != token

    anon = Actor(kind="anon", anon_hash=t.anon_token_hash)
    w = await as_actor(WARDEN)
    await act(t.id, Action.ASSIGN, w)
    await act(t.id, Action.START_REVIEW, w)
    await act(t.id, Action.RESOLVE, w, note="done")
    await act(t.id, Action.CONFIRM, anon)

    for e in await chain(t.id):
        assert str(student.id) not in e.actor
        assert str(student.id) not in str(e.payload)
    assert (await chain(t.id))[0].actor == "petitioner:anonymous"
    assert (await chain(t.id))[-1].actor == "petitioner:anonymous"


async def test_wrong_anonymous_token_is_rejected():
    t, _ = await file(anonymous=True)
    from app.security import hash_anonymous_token

    with pytest.raises(TicketError) as e:
        await act(t.id, Action.CONFIRM, Actor(kind="anon", anon_hash=hash_anonymous_token("vsh_guess")))
    assert e.value.status_code == 403


async def test_priority_scales_the_deadline():
    crit, _ = await file(priority="critical")
    low, _ = await file(priority="low")
    # Hostel L1 base = 48h; critical x0.25 = 12h, low x1.5 = 72h
    assert round((crit.due_at - crit.created_at).total_seconds() / 3600) == 12
    assert round((low.due_at - low.created_at).total_seconds() / 3600) == 72


async def test_unknown_category_rejected():
    with pytest.raises(TicketError):
        await file(category="Quidditch")
