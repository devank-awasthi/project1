"""Tamper-evidence: every way an insider could edit the record is detected."""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.canonical import canonical_json, format_ts, hash_payload, sha256_hex, utcnow
from app.db import SessionLocal, engine
from app.enums import Action
from app.models import LedgerEntry, Notification, TamperAlert, Ticket
from app.services import events, ledger
from app.services.ledger import compute_entry_hash
from app.services.tickets import add_comment, transition
from tests.conftest import as_actor, file, superuser_sql


async def build(n_comments=2):
    t, _ = await file()
    w = await as_actor("warden@vishanti.edu")
    async with SessionLocal() as s:
        await transition(s, t.id, Action.ASSIGN, w)
        await events.commit(s)
    for i in range(n_comments):
        async with SessionLocal() as s:
            await add_comment(s, t.id, w, f"update {i}")
            await events.commit(s)
    return t


async def report(ticket_id):
    async with SessionLocal() as s:
        return await ledger.verify_ticket(s, await s.get(Ticket, ticket_id))


# ---------------------------------------------------------- canonical form


def test_canonical_json_is_order_independent():
    assert canonical_json({"b": 1, "a": [1, {"d": 2, "c": "x"}]}) == canonical_json(
        {"a": [1, {"c": "x", "d": 2}], "b": 1}
    )
    assert canonical_json({"a": "é"}) == '{"a":"é"}'


def test_floats_are_refused():
    with pytest.raises(TypeError):
        canonical_json({"amount": 1.5})


def test_timestamp_format_is_fixed_width():
    assert len(format_ts(utcnow())) == len("2026-10-22T11:09:00.000000Z")


# ---------------------------------------------------------- database guard


@pytest.mark.parametrize(
    "stmt",
    ["UPDATE ledger_entries SET actor = 'x'", "DELETE FROM ledger_entries", "TRUNCATE ledger_entries CASCADE"],
)
async def test_append_only_trigger_blocks_mutation(stmt):
    await build(0)
    with pytest.raises(DBAPIError, match="append-only"):
        async with engine.begin() as conn:
            await conn.execute(text(stmt))


async def test_access_log_is_append_only_too():
    with pytest.raises(DBAPIError, match="append-only"):
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE access_logs"))


# ---------------------------------------------------------- verifier


async def test_clean_chain_verifies():
    t = await build()
    r = await report(t.id)
    assert r.ok, r
    assert r.entries == 4
    assert r.checks == [
        "hash chain intact",
        "head matches",
        "state replay matches",
        "complaint content matches",
        "attachment hashes match",
    ]


async def test_edited_payload_detected():
    t = await build()
    await superuser_sql(
        "UPDATE ledger_entries SET payload = jsonb_set(payload, '{text}', '\"nothing happened\"') WHERE ticket_id = :t AND seq = 2",
        params={"t": t.id},
    )
    r = await report(t.id)
    assert not r.ok and r.reason == "payload_altered" and r.failure_seq == 2


async def test_edited_actor_detected():
    t = await build()
    await superuser_sql(
        "UPDATE ledger_entries SET actor = 'official:Registrar:x' WHERE ticket_id = :t AND seq = 1", params={"t": t.id}
    )
    r = await report(t.id)
    assert r.reason == "entry_hash_mismatch" and r.failure_seq == 1


async def test_backdated_timestamp_detected():
    t = await build()
    await superuser_sql(
        "UPDATE ledger_entries SET ts = '2020-01-01T00:00:00.000000Z' WHERE ticket_id = :t AND seq = 1",
        params={"t": t.id},
    )
    assert (await report(t.id)).reason == "entry_hash_mismatch"


async def test_deleted_middle_entry_detected():
    t = await build()
    await superuser_sql("DELETE FROM ledger_entries WHERE ticket_id = :t AND seq = 2", params={"t": t.id})
    r = await report(t.id)
    assert r.reason == "sequence_gap" and r.failure_seq == 2


async def test_truncated_tail_detected():
    t = await build()
    await superuser_sql("DELETE FROM ledger_entries WHERE ticket_id = :t AND seq = 3", params={"t": t.id})
    assert (await report(t.id)).reason == "head_mismatch"


async def test_status_edited_directly_on_ticket_row_detected():
    t = await build()
    async with engine.begin() as conn:  # tickets is a normal table
        await conn.execute(text("UPDATE tickets SET status = 'CLOSED' WHERE id = :t"), {"t": t.id})
    r = await report(t.id)
    assert r.reason == "state_mismatch"


async def test_complaint_text_edit_detected():
    t = await build()
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE tickets SET description = 'minor issue, ignore' WHERE id = :t"), {"t": t.id})
    assert (await report(t.id)).reason == "content_altered"


async def test_verify_all_raises_alert_and_notifies_admin():
    t = await build()
    clean, _ = await file(title="Another complaint about the fan")
    await superuser_sql(
        "UPDATE ledger_entries SET actor = 'someone-else' WHERE ticket_id = :t AND seq = 1", params={"t": t.id}
    )
    async with SessionLocal() as s:
        run = await ledger.verify_all(s, "test")
        await events.commit(s)
    assert run.tickets_checked == 2 and run.failures == 1
    async with SessionLocal() as s:
        alerts = (await s.scalars(select(TamperAlert))).all()
        note = await s.scalar(select(Notification).where(Notification.event_type == "tamper_detected"))
    assert [a.ticket_ref for a in alerts] == [t.ref]
    assert note is not None and "Tamper Detected" in note.payload["message"]
    # a second run does not duplicate the open alert
    async with SessionLocal() as s:
        await ledger.verify_all(s, "test")
        await events.commit(s)
    async with SessionLocal() as s:
        assert len((await s.scalars(select(TamperAlert))).all()) == 1


# ---------------------------------------------------------- checkpoints


async def test_full_chain_rewrite_is_caught_by_checkpoint():
    """An attacker with superuser rights edits an entry AND recomputes every
    later hash so the chain looks perfect. Only the checkpoint catches it."""
    t = await build()
    async with SessionLocal() as s:
        cp = await ledger.create_checkpoint(s)
        await events.commit(s)
    assert len(cp.root_hash) == 64

    async with SessionLocal() as s:
        entries = (
            await s.scalars(select(LedgerEntry).where(LedgerEntry.ticket_id == t.id).order_by(LedgerEntry.seq))
        ).all()
        rows = [(e.seq, e.prev_hash, e.actor, e.action, dict(e.payload), e.ts) for e in entries]
    forged_payload = {"text": "Resolved to the petitioner's satisfaction"}
    prev = rows[0][1]
    stmts, params = [], {"t": t.id}
    for seq, _, actor, action, payload, ts in rows:
        if seq == 2:
            payload = forged_payload
        p_hash = hash_payload(payload)
        h = compute_entry_hash(
            ticket_id=str(t.id), seq=seq, prev_hash=prev, actor=actor, action=action, payload_hash=p_hash, ts=ts
        )
        stmts.append(
            f"UPDATE ledger_entries SET prev_hash = :p{seq}, hash = :h{seq}, payload = CAST(:j{seq} AS jsonb), payload_hash = :ph{seq} "
            f"WHERE ticket_id = :t AND seq = {seq}"
        )
        params.update({f"p{seq}": prev, f"h{seq}": h, f"j{seq}": canonical_json(payload), f"ph{seq}": p_hash})
        prev = h
    stmts.append("UPDATE tickets SET head_hash = :head WHERE id = :t")
    params["head"] = prev
    await superuser_sql(*stmts, params=params)

    assert (await report(t.id)).ok  # the chain alone is fooled...
    async with SessionLocal() as s:
        problems = await ledger.verify_checkpoints(s)
    assert problems == [
        {"checkpoint_id": cp.id, "reason": "history_rewritten_before_checkpoint"}
    ]  # ...the checkpoint is not


async def test_checkpoints_chain_to_each_other():
    await build()
    async with SessionLocal() as s:
        a = await ledger.create_checkpoint(s)
        await events.commit(s)
    await build()
    async with SessionLocal() as s:
        b = await ledger.create_checkpoint(s)
        await events.commit(s)
        assert b.prev_root == a.root_hash and b.root_hash != a.root_hash
        assert await ledger.verify_checkpoints(s) == []


def test_entry_hash_matches_documented_formula():
    material = {
        "v": 1,
        "ticket_id": "t",
        "seq": 0,
        "prev_hash": "0" * 64,
        "actor": "a",
        "action": "GENESIS",
        "payload_hash": "p",
        "ts": "2026-01-01T00:00:00.000000Z",
    }
    assert compute_entry_hash(
        ticket_id="t",
        seq=0,
        prev_hash="0" * 64,
        actor="a",
        action="GENESIS",
        payload_hash="p",
        ts="2026-01-01T00:00:00.000000Z",
    ) == sha256_hex(canonical_json(material))
