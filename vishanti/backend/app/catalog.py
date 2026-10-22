"""Reference data: the state machine and the default escalation matrix.

Both are loaded into tables (transition_rules, escalation_rules) by init_db.
After that the tables are the source of truth; admins edit escalation rules
from the admin panel.
"""

from app.enums import Action, ActorKind
from app.enums import TicketStatus as S

# (from_state, action, actor_kind, to_state, requires_assignee, description)
TRANSITIONS: list[tuple[str, str, str, str, bool, str]] = [
    (
        S.FILED,
        Action.ASSIGN,
        ActorKind.OFFICIAL,
        S.ASSIGNED,
        False,
        "Official at the current level picks up the ticket",
    ),
    (
        S.ESCALATED,
        Action.ASSIGN,
        ActorKind.OFFICIAL,
        S.ASSIGNED,
        False,
        "Higher authority picks up an escalated ticket",
    ),
    (
        S.ESCALATED,
        Action.AUTO_ASSIGN,
        ActorKind.SYSTEM,
        S.ASSIGNED,
        False,
        "System routes an escalated ticket to the least-loaded official",
    ),
    (S.ASSIGNED, Action.START_REVIEW, ActorKind.OFFICIAL, S.IN_REVIEW, True, "Assignee starts working on it"),
    (S.IN_REVIEW, Action.RESOLVE, ActorKind.OFFICIAL, S.RESOLVED, True, "Assignee proposes a resolution"),
    (S.RESOLVED, Action.CONFIRM, ActorKind.PETITIONER, S.CLOSED, False, "Petitioner accepts the resolution"),
    (
        S.RESOLVED,
        Action.DISPUTE,
        ActorKind.PETITIONER,
        S.ESCALATED,
        False,
        "Petitioner rejects it; reopened one level up",
    ),
    (S.RESOLVED, Action.AUTO_CLOSE, ActorKind.SYSTEM, S.CLOSED, False, "No response within the confirmation window"),
    *[
        (src, Action.SLA_ESCALATE, ActorKind.SYSTEM, S.ESCALATED, False, "SLA deadline passed")
        for src in (S.FILED, S.ASSIGNED, S.IN_REVIEW, S.ESCALATED)
    ],
]

# Authority level of each designation. An official holds exactly one.
DESIGNATION_LEVEL: dict[str, int] = {
    "Warden": 1,
    "Mess Manager": 1,
    "Head of Department": 1,
    "Bursar": 1,
    "Estate Officer": 1,
    "IT Cell Head": 1,
    "Librarian": 1,
    "ICC Chairperson": 1,
    "Student Affairs Officer": 1,
    "Dean of Students": 2,
    "Dean Academics": 2,
    "Finance Officer": 2,
    "Dean Planning": 2,
    "Registrar": 3,
}

# category -> [(level, designation, base SLA hours), ...]
ESCALATION_MATRIX: dict[str, list[tuple[int, str, int]]] = {
    "Hostel": [(1, "Warden", 48), (2, "Dean of Students", 72), (3, "Registrar", 96)],
    "Mess": [(1, "Mess Manager", 24), (2, "Dean of Students", 48), (3, "Registrar", 72)],
    "Academics": [(1, "Head of Department", 72), (2, "Dean Academics", 96), (3, "Registrar", 120)],
    "Fees & Finance": [(1, "Bursar", 72), (2, "Finance Officer", 96), (3, "Registrar", 120)],
    "Infrastructure": [(1, "Estate Officer", 72), (2, "Dean Planning", 96), (3, "Registrar", 120)],
    "IT & Network": [(1, "IT Cell Head", 24), (2, "Dean Planning", 48), (3, "Registrar", 72)],
    "Safety & Harassment": [(1, "ICC Chairperson", 24), (2, "Dean of Students", 24), (3, "Registrar", 24)],
    "Library": [(1, "Librarian", 72), (2, "Dean Academics", 96), (3, "Registrar", 120)],
    "Other": [(1, "Student Affairs Officer", 72), (2, "Dean of Students", 96), (3, "Registrar", 120)],
}

CATEGORIES = list(ESCALATION_MATRIX)
