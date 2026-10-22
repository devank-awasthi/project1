# Escalation cron verification report

Deliverable for Webster 2026 E3: evidence that SLA timers fire, escalate exactly once, survive failure, and are recorded in the audit chain.

## 1. What is being verified

| Claim | How it is guaranteed | Evidence |
|---|---|---|
| The clock starts at filing, not at assignment | `due_at` is set inside the same transaction that inserts the ticket in `FILED` | §3.3, `test_unassigned_ticket_still_escalates_and_is_routed` |
| A timer fires at the deadline | Celery task queued after commit with `eta = due_at` | §3.1, §4 |
| A lost task does not lose the escalation | Beat runs the sweeper every 60 s over `due_at <= now()` | §3.4 |
| No ticket is escalated twice | Row lock + "still due for this deadline?" check; `due_at` moves forward after escalating | §3.2, §3.5, §5 |
| Parallel workers do not collide | Sweeper takes rows with `FOR UPDATE SKIP LOCKED`, one per transaction | §3.5, §5 |
| The right authority receives it | Next level from `escalation_rules`; least-loaded official with that designation | §3.3 |
| The top of the ladder is handled | `OMBUDSPERSON_ALERT` entry, admin + petitioner notification, public flag | §3.6 |
| Every escalation is in the audit chain | `SLA_ESCALATE` by `system:sla-worker`, `AUTO_ASSIGN` by `system:router`; chain verifies | §3.3, §4 |

## 2. Mechanism

```
file / dispute / escalate ──commit──► queue  vishanti.escalate_ticket(ticket_id, expected_due)  eta = due_at
                                                     │
beat every 60 s ──► vishanti.sweep ──► SELECT id … WHERE status IN (open) AND due_at <= now()
                                       ORDER BY due_at LIMIT 1 FOR UPDATE SKIP LOCKED   (one per txn)
                                                     │
                     both call escalate_if_due(ticket_id, expected_due?)
                       lock row FOR UPDATE
                       status not open?                          → "not_open"
                       expected_due given and ≠ current due_at?  → "stale"
                       due_at > now?                             → "not_due"
                       top level?  → OMBUDSPERSON_ALERT          → "ombudsperson"
                       else SLA_ESCALATE (level+1, fresh timer) + AUTO_ASSIGN → "escalated"
```

Only "escalated" and "ombudsperson" change anything, and each moves `due_at` or sets `ombudsperson_alerted_at`, so the same deadline can never be acted on twice.

## 3. Automated tests (backend/tests/test_escalation.py, test_concurrency.py)

All run against real PostgreSQL 16 and Redis 7. Time is injected (`now=`) rather than waited for. Result at submission: **all pass**.

1. `test_new_ticket_queues_timer_task_at_due_time`: filing queues exactly one `vishanti.escalate_ticket` with `eta == due_at` and args `[ticket_id, due_at]`.
2. `test_not_due_and_stale_tasks_do_nothing`: one second before the deadline → `not_due`; a task carrying an old deadline → `stale`; level unchanged.
3. `test_unassigned_ticket_still_escalates_and_is_routed`: nobody touches a Hostel ticket; at the deadline it moves to level 2, is auto-assigned to the Dean of Students, gets a later `due_at`, `breach_count = 1`, chain = `GENESIS, SLA_ESCALATE, AUTO_ASSIGN`.
4. `test_sweeper_backstop_and_second_run_is_noop`: two overdue tickets and one not yet due; first sweep escalates 2, second sweep escalates 0, the third ticket stays at level 1.
5. `test_parallel_workers_escalate_once` / `test_parallel_sweepers_do_not_double_escalate`: 6 concurrent `escalate_if_due` calls → exactly one "escalated"; 3 concurrent sweepers over 8 overdue tickets → 8 escalations in total, every ticket `breach_count = 1`.
6. `test_full_ladder_then_ombudsperson`: IT & Network ticket, four successive deadlines → `escalated, escalated, ombudsperson, not_open`; final level 3, ombudsperson notification stored, chain verifies.
7. `test_duplicate_delivery_escalates_once`: the same ETA task delivered twice → `escalated`, then `stale`; an immediate sweep → `not_due`.
8. `test_resolved_ticket_is_not_escalated`, `test_resolved_ticket_auto_closes_after_confirmation_window`: the clock pauses in `RESOLVED`; auto-close fires only after the 72-hour window.
9. `test_sla_warning_sent_once_per_deadline`: warning at 20 % remaining ("SLA breach in 300 minutes"), not repeated.
10. `test_rule_change_applies_to_new_tickets_without_redeploy`: changing the Hostel L1 rule to 4 h gives the next ticket a 4 h deadline.
11. `test_celery_task_code_path_end_to_end`: runs the real Celery task body (`asyncio.run`, fresh engine) in a thread: first call `escalated`, second `stale`.
12. `test_resolve_and_escalate_race`: the assignee resolves at the same instant the worker fires; either outcome is consistent and the chain verifies.

## 4. Live run with Celery and Redis

API, worker and beat running; `SLA_HOUR_SECONDS=2` so a critical Mess ticket (24 h × 0.25 = 6 SLA hours) is due in 12 seconds.

```
VSH-000091 created 2026-10-22T11:49:36.626868+00:00 due 2026-10-22T11:49:48.626868+00:00

worker.log
[2026-10-22 11:49:48,733: INFO/ForkPoolWorker-2] escalate_ticket 4aa21408-… -> escalated

GET /api/tickets/{id}/ledger
0 GENESIS       student:cb471300-…   2026-10-22T11:49:36.626868Z
1 SLA_ESCALATE  system:sla-worker    2026-10-22T11:49:48.629710Z
2 AUTO_ASSIGN   system:router        2026-10-22T11:49:48.629710Z
```

The escalation was recorded 3 ms after the deadline.

Sweeper backstop on first start-up, with demo data that had been backdated (many tickets already overdue and no ETA tasks queued for them):

```
[2026-10-22 11:48:42,545: INFO/ForkPoolWorker-2] sweeper: {'escalated': 43, 'ombudsperson': 0, 'auto_closed': 0, 'errors': 0}
```

## 5. Load

`python -m scripts.bench --tickets 2000` (2 vCPU, everything on one machine):

```
filing: 2000 tickets in 12.03s = 166 tickets/s (16 concurrent writers)
sweep: 2000 overdue tickets escalated by 3 parallel sweepers in 24.71s = 81/s; per sweeper [668, 667, 665]; tickets not escalated exactly once: 0
verify: 2000 chains, 6000 entries in 3.27s = 1835 entries/s, failures 0
```

## 6. How to reproduce

```bash
cd backend
pytest tests/test_escalation.py tests/test_concurrency.py -v
python -m scripts.bench --tickets 2000          # uses its own database, vishanti_bench
# live: SLA_HOUR_SECONDS=2, start API + `celery -A app.celery_app worker -B`, file a critical Mess complaint, watch worker.log
```

## 7. Known limits

* Beat itself is a single scheduler process. If it is down, ETA tasks still fire; if both beat and the broker are lost, escalations resume on the next sweep after restart (from `due_at`, nothing is lost).
* Timer precision for the sweeper path is up to 60 s; the ETA path is near-immediate.
* Changing an escalation rule does not move deadlines already running; the new value applies from the next timer.
