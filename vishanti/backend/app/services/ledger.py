"""Per-ticket SHA-256 hash chain, its verifier, and daily checkpoints.

Entry hash
----------
    hash = SHA-256( canonical_json({
        "v": 1, "ticket_id": ..., "seq": n, "prev_hash": ...,
        "actor": ..., "action": ..., "payload_hash": ..., "ts": ...
    }) )
    payload_hash = SHA-256( canonical_json(payload) )

Entry 0 of every ticket is GENESIS with prev_hash = 64 zeros. `seq` is unique
per ticket, so two writers can never both append entry n (no forks), and the
ticket row is locked FOR UPDATE by every caller of `append`.

What the verifier proves
------------------------
Recomputing every hash detects an edited entry, an edited payload, a removed
or reordered entry, and a truncated tail (head mismatch). Replaying the
statuses in the chain and comparing them with the ticket row detects someone
editing `tickets.status` directly. The GENESIS entry commits to the complaint
text and attachment hashes, so editing those is caught too.

What it cannot prove on its own: someone with full control of the database can
rewrite a whole chain consistently. Daily checkpoints (a root hash over every
chain head, chained to the previous checkpoint and published outside the
database) limit that: a rewrite of history before the last published checkpoint
no longer matches it.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.canonical import GENESIS_PREV_HASH, canonical_json, format_ts, hash_payload, sha256_hex, utcnow
from app.config import settings
from app.enums import Action, TicketStatus
from app.models import Attachment, Checkpoint, LedgerEntry, TamperAlert, Ticket, VerificationRun
from app.services.notify import ADMIN_CHANNEL, notify

log = logging.getLogger("vishanti.ledger")
HASH_VERSION = 1


def compute_entry_hash(
    *, ticket_id: str, seq: int, prev_hash: str, actor: str, action: str, payload_hash: str, ts: str
) -> str:
    material = {
        "v": HASH_VERSION,
        "ticket_id": ticket_id,
        "seq": seq,
        "prev_hash": prev_hash,
        "actor": actor,
        "action": action,
        "payload_hash": payload_hash,
        "ts": ts,
    }
    return sha256_hex(canonical_json(material))


def content_hash(title: str, description: str, category: str, priority: str) -> str:
    return hash_payload({"title": title, "description": description, "category": category, "priority": priority})


async def append(
    session: AsyncSession,
    ticket: Ticket,
    *,
    actor: str,
    action: str,
    payload: dict,
    now=None,
) -> LedgerEntry:
    """Append one entry. Caller must hold the ticket row lock (or have just
    inserted the ticket in this transaction)."""
    seq = ticket.head_seq + 1
    prev_hash = ticket.head_hash or GENESIS_PREV_HASH
    if seq == 0 and action != Action.GENESIS:
        raise ValueError("first entry of a chain must be GENESIS")
    ts = format_ts(now or utcnow())
    p_hash = hash_payload(payload)
    entry_hash = compute_entry_hash(
        ticket_id=str(ticket.id), seq=seq, prev_hash=prev_hash, actor=actor, action=action, payload_hash=p_hash, ts=ts
    )
    entry = LedgerEntry(
        ticket_id=ticket.id,
        seq=seq,
        prev_hash=prev_hash,
        hash=entry_hash,
        actor=actor,
        action=action,
        from_status=payload.get("from"),
        to_status=payload.get("to"),
        payload=payload,
        payload_hash=p_hash,
        ts=ts,
    )
    session.add(entry)
    ticket.head_hash = entry_hash
    ticket.head_seq = seq
    await session.flush()
    return entry


# --------------------------------------------------------------------------- verify


@dataclass
class ChainReport:
    ticket_id: str
    ref: str
    ok: bool
    entries: int
    head_hash: str | None
    reason: str | None = None
    failure_seq: int | None = None
    detail: str | None = None
    checks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _fail(report: ChainReport, reason: str, seq: int | None, detail: str) -> ChainReport:
    report.ok = False
    report.reason = reason
    report.failure_seq = seq
    report.detail = detail
    return report


async def verify_ticket(session: AsyncSession, ticket: Ticket) -> ChainReport:
    entries = (
        await session.scalars(select(LedgerEntry).where(LedgerEntry.ticket_id == ticket.id).order_by(LedgerEntry.seq))
    ).all()
    report = ChainReport(ticket_id=str(ticket.id), ref=ticket.ref, ok=True, entries=len(entries), head_hash=None)
    if not entries:
        return _fail(report, "missing_genesis", 0, "ticket has no ledger entries")
    if entries[0].action != Action.GENESIS:
        return _fail(report, "missing_genesis", entries[0].seq, "first entry is not GENESIS")

    expected_prev = GENESIS_PREV_HASH
    status: str | None = None
    level: int | None = None
    for i, e in enumerate(entries):
        if e.seq != i:
            return _fail(report, "sequence_gap", i, f"expected seq {i}, found {e.seq} (entry removed or reordered)")
        if e.prev_hash != expected_prev:
            return _fail(report, "broken_link", e.seq, "prev_hash does not match the previous entry's hash")
        if hash_payload(e.payload) != e.payload_hash:
            return _fail(report, "payload_altered", e.seq, "payload no longer matches payload_hash")
        recomputed = compute_entry_hash(
            ticket_id=str(e.ticket_id),
            seq=e.seq,
            prev_hash=e.prev_hash,
            actor=e.actor,
            action=e.action,
            payload_hash=e.payload_hash,
            ts=e.ts,
        )
        if recomputed != e.hash:
            return _fail(report, "entry_hash_mismatch", e.seq, "stored hash differs from recomputed hash")
        if (e.payload.get("from"), e.payload.get("to")) != (e.from_status, e.to_status):
            return _fail(report, "column_mismatch", e.seq, "status columns differ from the hashed payload")
        if e.payload.get("to"):
            status = e.payload["to"]
        if "level" in e.payload:
            level = e.payload["level"]
        expected_prev = e.hash
    report.checks.append("hash chain intact")

    last = entries[-1]
    report.head_hash = last.hash
    if ticket.head_hash != last.hash or ticket.head_seq != last.seq:
        return _fail(report, "head_mismatch", last.seq, "ticket head does not match the last entry (tail truncated?)")
    report.checks.append("head matches")

    if status != ticket.status or level != ticket.current_level:
        return _fail(
            report,
            "state_mismatch",
            None,
            f"chain replays to {status}/L{level} but ticket row says {ticket.status}/L{ticket.current_level}",
        )
    report.checks.append("state replay matches")

    genesis = entries[0].payload
    if genesis.get("content_sha256") != content_hash(
        ticket.title, ticket.description, ticket.category, ticket.priority
    ):
        return _fail(report, "content_altered", 0, "complaint text no longer matches the hash in GENESIS")
    report.checks.append("complaint content matches")

    files = (await session.scalars(select(Attachment).where(Attachment.ticket_id == ticket.id))).all()
    on_record = {a["id"]: a["sha256"] for a in genesis.get("attachments", [])}
    current = {str(a.id): a.sha256 for a in files}
    if on_record != current:
        return _fail(report, "attachment_altered", 0, "attachment hashes differ from GENESIS")
    report.checks.append("attachment hashes match")
    return report


# ----------------------------------------------------------------------- checkpoints


async def compute_root(session: AsyncSession, max_entry_id: int, prev_root: str) -> tuple[str, int]:
    rows = (
        await session.execute(
            text(
                """
                SELECT DISTINCT ON (ticket_id) ticket_id::text, seq, hash
                FROM ledger_entries WHERE id <= :m
                ORDER BY ticket_id, seq DESC
                """
            ),
            {"m": max_entry_id},
        )
    ).all()
    heads = sorted([[r[0], r[1], r[2]] for r in rows])
    root = sha256_hex(
        canonical_json({"v": HASH_VERSION, "max_entry_id": max_entry_id, "prev_root": prev_root, "heads": heads})
    )
    return root, len(heads)


async def _anchor(checkpoint: Checkpoint) -> str | None:
    """Publish the root hash outside our infrastructure (a public GitHub gist).
    Each checkpoint is a new file; the gist's own revision history means we
    cannot quietly change an old one either."""
    if not (settings.GITHUB_TOKEN and settings.CHECKPOINT_GIST_ID):
        return None
    body = canonical_json(
        {
            "day": checkpoint.day.isoformat(),
            "root_hash": checkpoint.root_hash,
            "prev_root": checkpoint.prev_root,
            "max_entry_id": checkpoint.max_entry_id,
            "ticket_count": checkpoint.ticket_count,
        }
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.patch(
                f"https://api.github.com/gists/{settings.CHECKPOINT_GIST_ID}",
                headers={"Authorization": f"Bearer {settings.GITHUB_TOKEN}", "Accept": "application/vnd.github+json"},
                json={
                    "files": {
                        f"vishanti-checkpoint-{checkpoint.day.isoformat()}-{checkpoint.max_entry_id}.json": {
                            "content": body
                        }
                    }
                },
            )
            r.raise_for_status()
            return r.json().get("html_url")
    except Exception as exc:
        log.warning("checkpoint anchoring failed: %s", exc)
        return None


async def create_checkpoint(session: AsyncSession, day: date | None = None) -> Checkpoint:
    max_id = await session.scalar(select(func.coalesce(func.max(LedgerEntry.id), 0)))
    prev = await session.scalar(select(Checkpoint).order_by(Checkpoint.id.desc()).limit(1))
    prev_root = prev.root_hash if prev else GENESIS_PREV_HASH
    root, count = await compute_root(session, max_id, prev_root)
    cp = Checkpoint(
        day=day or utcnow().date(), max_entry_id=max_id, ticket_count=count, prev_root=prev_root, root_hash=root
    )
    cp.anchor_url = await _anchor(cp)
    session.add(cp)
    await session.flush()
    return cp


async def verify_checkpoints(session: AsyncSession) -> list[dict]:
    """Recompute every stored checkpoint from the ledger as it is now."""
    problems = []
    prev_root = GENESIS_PREV_HASH
    for cp in (await session.scalars(select(Checkpoint).order_by(Checkpoint.id))).all():
        if cp.prev_root != prev_root:
            problems.append({"checkpoint_id": cp.id, "reason": "checkpoint_chain_broken"})
        root, _ = await compute_root(session, cp.max_entry_id, cp.prev_root)
        if root != cp.root_hash:
            problems.append({"checkpoint_id": cp.id, "reason": "history_rewritten_before_checkpoint"})
        prev_root = cp.root_hash
    return problems


# ------------------------------------------------------------------------ full run


async def verify_all(session: AsyncSession, triggered_by: str = "scheduler") -> VerificationRun:
    started = utcnow()
    failures: list[ChainReport] = []
    tickets_checked = entries_checked = 0
    last_id = None
    while True:
        q = select(Ticket).order_by(Ticket.id).limit(500)
        if last_id is not None:
            q = q.where(Ticket.id > last_id)
        batch = (await session.scalars(q)).all()
        if not batch:
            break
        for t in batch:
            report = await verify_ticket(session, t)
            tickets_checked += 1
            entries_checked += report.entries
            if not report.ok:
                failures.append(report)
        last_id = batch[-1].id

    cp_problems = await verify_checkpoints(session)

    new_alerts = []
    for f in failures:
        exists = await session.scalar(
            select(TamperAlert.id).where(
                TamperAlert.ticket_id == uuid.UUID(f.ticket_id),
                TamperAlert.reason == f.reason,
                TamperAlert.acknowledged.is_(False),
            )
        )
        if not exists:
            alert = TamperAlert(
                ticket_id=uuid.UUID(f.ticket_id),
                ticket_ref=f.ref,
                reason=f.reason,
                detail=f.detail or "",
                failure_seq=f.failure_seq,
            )
            session.add(alert)
            new_alerts.append(alert)
    for p in cp_problems:
        session.add(TamperAlert(reason=p["reason"], detail=f"checkpoint #{p['checkpoint_id']}"))

    run = VerificationRun(
        started_at=started,
        finished_at=utcnow(),
        tickets_checked=tickets_checked,
        entries_checked=entries_checked,
        failures=len(failures),
        checkpoints_ok=not cp_problems,
        triggered_by=triggered_by,
    )
    session.add(run)
    await session.flush()
    if new_alerts or cp_problems:
        await notify(
            session,
            [ADMIN_CHANNEL],
            "tamper_detected",
            {
                "message": f"Tamper Detected: {len(failures)} chain(s), {len(cp_problems)} checkpoint issue(s)",
                "tickets": [{"ref": f.ref, "reason": f.reason, "seq": f.failure_seq} for f in failures],
            },
        )
    return run


__all__ = [
    "append",
    "verify_ticket",
    "verify_all",
    "create_checkpoint",
    "verify_checkpoints",
    "compute_entry_hash",
    "content_hash",
    "ChainReport",
    "TicketStatus",
]
