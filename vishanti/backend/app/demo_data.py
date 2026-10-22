"""Synthetic demo data, filed through the real services so every ticket has a
valid hash chain. Timestamps are backdated over the last five months so the
transparency dashboard has enough cases per cell to clear suppression.

Clearly synthetic: no real students, no real complaints.
"""

from __future__ import annotations

import random
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.actors import Actor
from app.canonical import utcnow
from app.enums import Action, Role, TicketStatus
from app.models import Ticket, User
from app.services import events
from app.services.embeddings import embed_and_cluster
from app.services.escalation import escalate_if_due
from app.services.tickets import create_ticket, get_rule, transition

TEMPLATES: dict[str, list[tuple[str, str]]] = {
    "Hostel": [
        (
            "Water leakage in hostel room ceiling",
            "Water has been leaking from the ceiling of my hostel room for a week and the mattress is wet.",
        ),
        (
            "Hostel room fan not working",
            "The ceiling fan in my hostel room stopped working and the room is unbearable at night.",
        ),
        (
            "No hot water in hostel bathrooms",
            "The geysers in the hostel bathrooms on the second floor have not worked for days.",
        ),
        (
            "Broken window latch in hostel room",
            "The window latch is broken so the window cannot be closed and insects come in.",
        ),
    ],
    "Mess": [
        (
            "Mess food quality very poor",
            "The food served in the mess has been stale and undercooked for the past week.",
        ),
        ("Insects found in mess food", "Insects were found in the dal served at dinner in the mess yesterday."),
        ("Mess timings not followed", "The mess closes before the posted dinner time and students are turned away."),
    ],
    "Academics": [
        (
            "Internal marks not updated on portal",
            "My internal assessment marks for this semester are still not updated on the portal.",
        ),
        (
            "Lab sessions cancelled repeatedly",
            "Our lab sessions have been cancelled three weeks in a row without any make-up class.",
        ),
        ("Attendance marked wrongly", "I attended all lectures last week but the portal shows me absent for two days."),
    ],
    "Fees & Finance": [
        (
            "Fee payment deducted but not reflected",
            "My fee payment was deducted from the bank account but the portal still shows it as pending.",
        ),
        (
            "Scholarship amount not credited",
            "The scholarship amount for this semester has not been credited to my account yet.",
        ),
        ("Hostel fee refund pending", "My hostel fee refund after vacating the room has been pending for two months."),
    ],
    "Infrastructure": [
        (
            "Classroom projector broken",
            "The projector in lecture hall 3 has been broken for two weeks and lectures are affected.",
        ),
        (
            "Street lights not working near library road",
            "Street lights on the road to the library are off, making it unsafe at night.",
        ),
    ],
    "IT & Network": [
        (
            "Wi-Fi not working in hostel block C",
            "The Wi-Fi in hostel block C has been down since Monday and we cannot submit assignments.",
        ),
        (
            "Hostel Wi-Fi down in block C again",
            "Wi-Fi is not working in hostel block C again, the connection drops every few minutes.",
        ),
        (
            "Wi-Fi in block C hostel keeps dropping",
            "Hostel block C Wi-Fi keeps dropping and is not working for online classes.",
        ),
        ("Portal login not working", "I cannot log in to the student portal; it says invalid session every time."),
    ],
    "Library": [
        (
            "Library closes early during exams",
            "The library is closing at 8 pm during exam week although the notice said midnight.",
        ),
        ("Requested books not available", "The books recommended for the course are not available in the library."),
    ],
    "Safety & Harassment": [
        (
            "Unsafe behaviour near the main gate",
            "A group of outsiders loiters near the main gate at night and passes remarks at students.",
        ),
    ],
    "Other": [
        (
            "Sports ground not maintained",
            "The sports ground is not maintained and the grass has not been cut for months.",
        ),
        (
            "Drinking water cooler not working",
            "The drinking water cooler near the academic block has not been working for days.",
        ),
    ],
}


async def _official(session, category: str, level: int) -> User:
    rule = await get_rule(session, category, level)
    return await session.scalar(
        select(User).where(
            User.role == Role.OFFICIAL, User.designation == rule.handler_designation, User.level == level
        )
    )


async def _progress(s, rng, tid, category, petitioner, filed_at, now) -> None:
    fate = rng.random()
    t = filed_at
    if fate < 0.18 and filed_at < now - timedelta(days=6):
        # nobody picked it up: the SLA worker escalates it
        tk = await s.get(Ticket, tid)
        await escalate_if_due(s, tid, now=tk.due_at + timedelta(minutes=5))
        await events.commit(s)
        return
    if fate < 0.30:
        return  # still FILED
    off = await _official(s, category, 1)
    steps = [
        (Action.ASSIGN, off, None, (1, 20), 0.38),
        (Action.START_REVIEW, off, None, (1, 12), 0.48),
        (Action.RESOLVE, off, "Issue fixed and verified on site.", (2, 40), None),
    ]
    for action, who, note, (lo, hi), stop_below in steps:
        t += timedelta(hours=rng.uniform(lo, hi))
        if t > now:
            return
        await transition(s, tid, action, Actor(kind="user", user=who), note=note, now=t)
        await events.commit(s)
        if stop_below is not None and fate < stop_below:
            return
    t += timedelta(hours=rng.uniform(1, 30))
    if t > now:
        return
    if fate < 0.56:
        await transition(s, tid, Action.DISPUTE, petitioner, note="The problem came back the next day.", now=t)
    else:
        await transition(s, tid, Action.CONFIRM, petitioner, now=t)
    await events.commit(s)


async def seed_demo_tickets(maker: async_sessionmaker, n: int = 90, seed: int = 7) -> None:
    rng = random.Random(seed)
    events.task_sink = []  # don't queue Celery tasks for backdated tickets
    now = utcnow()
    async with maker() as s:
        if await s.scalar(select(Ticket.id).limit(1)):
            print("demo tickets already present; skipping")
            return
        students = (await s.scalars(select(User).where(User.role == Role.STUDENT))).all()

    weights = {
        "Hostel": 18,
        "Mess": 14,
        "Academics": 12,
        "Fees & Finance": 12,
        "IT & Network": 16,
        "Infrastructure": 8,
        "Library": 6,
        "Safety & Harassment": 3,
        "Other": 6,
    }
    cats = list(weights)
    for i in range(n):
        category = rng.choices(cats, weights=[weights[c] for c in cats])[0]
        title, desc = rng.choice(TEMPLATES[category])
        filed_at = now - timedelta(days=rng.uniform(1, 150), hours=rng.uniform(0, 23))
        anonymous = category == "Safety & Harassment" or rng.random() < 0.15
        priority = rng.choices(["low", "medium", "high", "critical"], weights=[2, 5, 3, 1])[0]
        student = rng.choice(students)

        async with maker() as s:
            ticket, raw = await create_ticket(
                s,
                actor=Actor(kind="user", user=student),
                title=title,
                description=desc,
                category=category,
                priority=priority,
                anonymous=anonymous,
                idempotency_key=f"demo-{uuid.uuid4()}",
                now=filed_at,
            )
            await events.commit(s)
            tid = ticket.id
        petitioner = (
            Actor(kind="anon", anon_hash=ticket.anon_token_hash) if anonymous else Actor(kind="user", user=student)
        )

        try:
            async with maker() as s:
                await _progress(s, rng, tid, category, petitioner, filed_at, now)
        except Exception as exc:  # keep seeding even if one path is not allowed
            print(f"demo ticket {i}: {exc}")

        async with maker() as s:
            await embed_and_cluster(s, tid)
            await events.commit(s)
    events.task_sink = None
    print(f"seeded {n} demo tickets")


__all__ = ["seed_demo_tickets", "TicketStatus"]
