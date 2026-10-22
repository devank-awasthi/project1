"""Database schema. Relationships are deliberately not mapped: every query is
explicit, so a `SELECT ... FOR UPDATE` never accidentally joins other tables."""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Sequence,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

EMBEDDING_DIM = 384

ticket_ref_seq = Sequence("ticket_ref_seq", metadata=Base.metadata)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20))  # student | official | admin
    # Officials only: authority level (1 = first responder ... 3 = Registrar)
    level: Mapped[int] = mapped_column(Integer, default=0)
    designation: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reg_no: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _created()


class EscalationRule(Base):
    """Who handles a category at each level, and how long they get.
    Policy lives here as data, so it can change without a redeploy."""

    __tablename__ = "escalation_rules"
    __table_args__ = (UniqueConstraint("category", "level"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[str] = mapped_column(String(80), index=True)
    level: Mapped[int] = mapped_column(Integer)
    handler_designation: Mapped[str] = mapped_column(String(100))
    base_sla_hours: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TransitionRule(Base):
    """The state machine as a table: (from_state, action, actor_kind) -> to_state."""

    __tablename__ = "transition_rules"
    __table_args__ = (UniqueConstraint("from_state", "action", "actor_kind"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    from_state: Mapped[str] = mapped_column(String(20))
    action: Mapped[str] = mapped_column(String(30))
    actor_kind: Mapped[str] = mapped_column(String(20))
    to_state: Mapped[str] = mapped_column(String(20))
    # Extra guard: the official must be the current assignee.
    requires_assignee: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str] = mapped_column(String(200), default="")


class ComplaintCluster(Base):
    __tablename__ = "complaint_clusters"

    id: Mapped[uuid.UUID] = _uuid_pk()
    category: Mapped[str] = mapped_column(String(80))
    centroid = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    size: Mapped[int] = mapped_column(Integer, default=0)
    sentiment_sum: Mapped[float] = mapped_column(Float, default=0.0)
    label: Mapped[str] = mapped_column(String(200), default="")
    systemic_alerted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        # Exactly one way to prove ownership: an account or an anonymous token.
        CheckConstraint("(petitioner_id IS NOT NULL) <> (anon_token_hash IS NOT NULL)", name="one_petitioner_kind"),
        Index(
            "ix_tickets_open_due", "due_at", postgresql_where="status IN ('FILED','ASSIGNED','IN_REVIEW','ESCALATED')"
        ),
        Index("ix_tickets_queue", "category", "current_level", "status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    ref: Mapped[str] = mapped_column(String(20), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(80))
    priority: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), index=True)
    current_level: Mapped[int] = mapped_column(Integer, default=1)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)

    petitioner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    anon_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)

    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timer_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sla_warned_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    breach_count: Mapped[int] = mapped_column(Integer, default=0)
    ombudsperson_alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirm_by: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)

    # Head of this ticket's hash chain (denormalised for fast appends).
    head_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    head_seq: Mapped[int] = mapped_column(Integer, default=-1)

    embedding = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    sentiment: Mapped[float | None] = mapped_column(Float, nullable=True)
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("complaint_clusters.id"), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LedgerEntry(Base):
    """Append-only. UPDATE / DELETE / TRUNCATE are rejected by a trigger."""

    __tablename__ = "ledger_entries"
    __table_args__ = (UniqueConstraint("ticket_id", "seq", name="uq_ledger_ticket_seq"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)
    actor: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(30))
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    payload_hash: Mapped[str] = mapped_column(String(64))
    ts: Mapped[str] = mapped_column(String(32))  # exactly the string that was hashed


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[uuid.UUID] = _uuid_pk()
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"), index=True)
    object_key: Mapped[str] = mapped_column(String(300))
    filename: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()


class Notification(Base):
    """Stored so SSE clients can replay what they missed (Last-Event-ID)."""

    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_channel_id", "channel", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(160))
    event_type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()


class Checkpoint(Base):
    """Daily root hash over every chain head. Append-only, and each checkpoint
    commits to the previous one, so the checkpoints form their own chain."""

    __tablename__ = "checkpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[datetime] = mapped_column(Date)
    max_entry_id: Mapped[int] = mapped_column(BigInteger)
    ticket_count: Mapped[int] = mapped_column(Integer)
    prev_root: Mapped[str] = mapped_column(String(64))
    root_hash: Mapped[str] = mapped_column(String(64))
    anchor_url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = _created()


class VerificationRun(Base):
    __tablename__ = "verification_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    tickets_checked: Mapped[int] = mapped_column(Integer)
    entries_checked: Mapped[int] = mapped_column(Integer)
    failures: Mapped[int] = mapped_column(Integer)
    checkpoints_ok: Mapped[bool] = mapped_column(Boolean)
    triggered_by: Mapped[str] = mapped_column(String(60))


class TamperAlert(Base):
    __tablename__ = "tamper_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    ticket_ref: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reason: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str] = mapped_column(Text)
    failure_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = _created()
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)


class AccessLog(Base):
    """Officials are audited too: every view and list by staff is recorded.
    Append-only, same trigger as the ledger."""

    __tablename__ = "access_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    designation: Mapped[str] = mapped_column(String(100))
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(40))
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _created()
