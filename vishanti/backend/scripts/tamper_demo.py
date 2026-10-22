"""Viva demo: play a corrupt insider with database superuser rights.

    python -m scripts.tamper_demo VSH-000092            # rewrite the last note/comment
    python -m scripts.tamper_demo VSH-000092 --status   # set the ticket to CLOSED behind the ledger's back
    python -m scripts.tamper_demo VSH-000092 --delete   # delete the newest ledger entry

The application role can never do this (the append-only trigger and the
REVOKE in schema_sql.py stop it). This script connects as the database owner
and switches the trigger off first, exactly what a rogue DBA would do. Then
open the complaint and press "Verify record", or wait for the scheduled
verifier to raise a Tamper Detected alert in the admin panel.
"""

import asyncio
import sys

from sqlalchemy import text

from app.config import settings
from app.db import make_engine

engine = make_engine(settings.MIGRATE_DATABASE_URL or settings.DATABASE_URL, nullpool=True)


async def main(ref: str, mode: str) -> None:
    async with engine.begin() as conn:
        tid = await conn.scalar(text("SELECT id FROM tickets WHERE ref = :r"), {"r": ref})
        if tid is None:
            sys.exit(f"no ticket {ref}")
        if mode == "--status":
            await conn.execute(text("UPDATE tickets SET status = 'CLOSED' WHERE id = :t"), {"t": tid})
            print(f"{ref}: status set to CLOSED directly on the tickets table")
            return
        await conn.execute(text("ALTER TABLE ledger_entries DISABLE TRIGGER USER"))
        if mode == "--delete":
            seq = await conn.scalar(text("SELECT max(seq) FROM ledger_entries WHERE ticket_id = :t"), {"t": tid})
            await conn.execute(
                text("DELETE FROM ledger_entries WHERE ticket_id = :t AND seq = :s"), {"t": tid, "s": seq}
            )
            print(f"{ref}: deleted ledger entry {seq}")
        else:
            row = (
                await conn.execute(
                    text(
                        """SELECT seq, CASE WHEN payload ? 'note' THEN 'note' ELSE 'text' END FROM ledger_entries
                           WHERE ticket_id = :t AND (payload ? 'note' OR payload ? 'text') ORDER BY seq DESC LIMIT 1"""
                    ),
                    {"t": tid},
                )
            ).first()
            if row is None:
                sys.exit("no note or comment to rewrite on this ticket")
            seq, key = row
            await conn.execute(
                text(
                    f"UPDATE ledger_entries SET payload = jsonb_set(payload, '{{{key}}}', to_jsonb(CAST(:v AS text))) WHERE ticket_id = :t AND seq = :s"
                ),
                {"t": tid, "s": seq, "v": "Petitioner was satisfied, no further action needed."},
            )
            print(f"{ref}: rewrote the {key} in ledger entry {seq}")
        await conn.execute(text("ALTER TABLE ledger_entries ENABLE TRIGGER USER"))
    await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ""))
