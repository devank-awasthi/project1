# Viva demo script (about 8 minutes)

Start everything with `docker compose up --build` (or the manual steps in the README) and keep `SLA_HOUR_SECONDS=60`, so one SLA hour is one minute. Open two browser windows: a normal one and a private one.

## 1. File anonymously (1 min)

Private window → http://localhost:3000 → **File anonymously**.
Category *Mess*, priority *Critical*, attach a phone photo.

Point out:
* The panel on the right: "Mess Manager has 6 hours" (6 minutes in demo mode).
* The attachment line: "Metadata removed. Fingerprint …". Location data is stripped before the file is sealed.
* The token screen: shown once, only its hash is stored.

## 2. Watch it climb on its own (2 min)

Normal window → sign in as `mess.manager@vishanti.edu`. Do nothing.
After 6 minutes the complaint leaves the Mess Manager's desk and appears on `dean.of.students@vishanti.edu`'s desk, already assigned. The anonymous window gets a live toast and the record shows *Deadline missed, escalated* by **Deadline enforcer** followed by *Routed to the next authority*.

Shortcut: Admin → Integrity → **Run deadline sweep now** runs the same code the scheduler runs.

## 3. Resolve, dispute (1 min)

As the Dean: Pick up → Start review → Mark resolved with a note.
In the anonymous window: the clock now says "to confirm or dispute". Press **Dispute and send it up**: it moves to the Registrar.

## 4. Verify, then tamper (2 min)

On the complaint page press **Verify record**. Each link turns green as the browser recomputes the SHA-256 seals; then the server's checks are listed.

Now play the corrupt insider (database owner):

```bash
docker compose exec api python -m scripts.tamper_demo VSH-0000xx            # rewrites the last note
# or --status (edit the ticket row), --delete (remove the newest entry)
```

Press **Verify record** again: the edited entry turns red, its seal is struck through, "content changed after it was sealed". Admin → Integrity → **Verify every record now** raises a Tamper Detected alert and a live toast.

Explain why the app itself could not do this: the API connects as `vishanti_app`, which has no UPDATE/DELETE on the ledger and does not own the table, so it cannot switch the trigger off. The script uses the owner account on purpose.

## 5. The attacker who rewrites everything (1 min)

"What if the DBA recomputes every hash after the edit?" Show `test_full_chain_rewrite_is_caught_by_checkpoint` in `backend/tests/test_ledger.py`: the chain check is fooled, the published checkpoint is not. Admin → Integrity → **Publish checkpoint now**, then show the root hash on the Public record page (and in the gist if configured).

## 6. Public record and accountability (1 min)

* http://localhost:3000/record: counts under 5 show as *hidden*; top-level breaches are listed in red.
* In the anonymous window, "Who has opened this complaint" lists every official view.
* Admin → Access log: every staff view and every rule change.

## Likely questions

**Why Postgres and not MongoDB?** We need `SELECT … FOR UPDATE`, unique constraints and triggers in the same transaction as the ledger append. That is what makes "no lost update, no forked chain" a database guarantee rather than an application promise.

**Why a chain per ticket and not one global chain?** A global chain makes every write in the institution contend for one lock (the "last hash"). Per ticket, unrelated complaints never block each other, and a fork is impossible because `(ticket_id, seq)` is unique.

**Why Celery ETA *and* a sweeper?** The ETA task is fast; the sweeper is the backstop if Redis loses the task or a worker dies. Both are safe to run together because escalation is idempotent per deadline.

**Why SSE and not WebSockets?** Updates only go server → client. SSE is plain HTTP, reconnects automatically and has Last-Event-ID built in, which we use to replay missed events from Postgres.

**Is the anonymous petitioner really anonymous?** The ticket has no user id (a database CHECK constraint enforces "account xor token"), the ledger actor is `petitioner:anonymous`, the token is stored only as a hash, attachments are stripped, and no IPs are logged. A determined college could still guess from the complaint's content; we cannot fix that in software.

**Hardest decision?** Making the petitioner, not the official, close a ticket. It is what stops an official from "resolving" a complaint to stop the clock, and it is why `RESOLVED` pauses the SLA instead of ending it.
