from enum import StrEnum


class TicketStatus(StrEnum):
    FILED = "FILED"
    ASSIGNED = "ASSIGNED"
    IN_REVIEW = "IN_REVIEW"
    RESOLVED = "RESOLVED"
    ESCALATED = "ESCALATED"
    CLOSED = "CLOSED"


# Statuses in which the SLA clock is running.
OPEN_STATUSES = (
    TicketStatus.FILED,
    TicketStatus.ASSIGNED,
    TicketStatus.IN_REVIEW,
    TicketStatus.ESCALATED,
)


class Role(StrEnum):
    STUDENT = "student"
    OFFICIAL = "official"
    ADMIN = "admin"


class ActorKind(StrEnum):
    """Who is allowed to fire a transition (see transition_rules)."""

    OFFICIAL = "official"
    PETITIONER = "petitioner"
    SYSTEM = "system"


class Priority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# Multiplier applied to the category's base SLA hours.
PRIORITY_SCALE: dict[str, float] = {
    Priority.CRITICAL: 0.25,
    Priority.HIGH: 0.5,
    Priority.MEDIUM: 1.0,
    Priority.LOW: 1.5,
}


class Action(StrEnum):
    GENESIS = "GENESIS"
    ASSIGN = "ASSIGN"
    AUTO_ASSIGN = "AUTO_ASSIGN"
    START_REVIEW = "START_REVIEW"
    RESOLVE = "RESOLVE"
    CONFIRM = "CONFIRM"
    DISPUTE = "DISPUTE"
    AUTO_CLOSE = "AUTO_CLOSE"
    SLA_ESCALATE = "SLA_ESCALATE"
    COMMENT = "COMMENT"
    OMBUDSPERSON_ALERT = "OMBUDSPERSON_ALERT"
