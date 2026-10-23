"""All SQLAlchemy ORM models for RaccBuddy in a single file."""

from __future__ import annotations

import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.core.config import settings

# Single source of truth for embedding vector dimensions — sourced from config.
EMBED_DIMENSIONS: int = settings.embed_dimensions


class Base(DeclarativeBase):
    """Base model for all database tables."""


# ---------------------------------------------------------------------------
# Core domain models
# ---------------------------------------------------------------------------


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "contact_handle",
            "platform",
            name="uq_owner_contact_handle",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    contact_handle: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    contact_name: Mapped[str] = mapped_column(String(200), nullable=False)
    platform: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="telegram",
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index(
            "uq_messages_provider_key",
            "platform",
            "external_chat_id",
            "external_message_id",
            unique=True,
            postgresql_where=sa_text("external_message_id IS NOT NULL"),
        ),
        Index("ix_messages_chat_timestamp", "chat_id", "timestamp"),
        Index("ix_messages_contact_timestamp", "from_contact_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    from_contact_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("contacts.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    is_bot_reply: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_from_owner: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    # Provider identifiers. Null on rows written before history sync.
    external_chat_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    external_message_id: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
    )
    # When the provider says the message was sent. Distinct from ingested_at.
    timestamp: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    edited_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    ingested_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=True,
    )


class SyncCursor(Base):
    """Per-provider, per-chat watermark for history backfill and incremental sync.

    ``newest_at`` is the latest original timestamp stored for this scope.
    Incremental sync re-reads a short overlap before that instant and relies
    on the provider-message unique key to drop duplicates. ``backfill_complete``
    stays false while the provider still reports a full page, so raising the
    page cap on a later run can pull older messages.
    """

    __tablename__ = "sync_cursors"
    __table_args__ = (
        UniqueConstraint("platform", "scope", name="uq_sync_cursor_scope"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    scope: Mapped[str] = mapped_column(String(512), nullable=False)
    newest_external_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    newest_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    oldest_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    backfill_complete: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class Summary(Base):
    __tablename__ = "summaries"
    __table_args__ = (
        Index(
            "ix_summaries_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_where=sa_text("embedding IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("contacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding = mapped_column(Vector(EMBED_DIMENSIONS), nullable=True)


class Relationship(Base):
    __tablename__ = "relationships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("contacts.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    score: Mapped[int] = mapped_column(Integer, default=50)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)


class Habit(Base):
    __tablename__ = "habits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True, default=0
    )
    trigger: Mapped[str] = mapped_column(String(200), nullable=False)
    frequency: Mapped[float] = mapped_column(Float, default=0.0)
    correlation: Mapped[float] = mapped_column(Float, default=0.0)
    last_detected: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False, default="general")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class PersistentUserState(Base):
    __tablename__ = "user_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, unique=True, index=True
    )
    mood: Mapped[str] = mapped_column(String(50), nullable=False, default="neutral")
    last_active: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    message_count_today: Mapped[int] = mapped_column(Integer, default=0)
    streak_days: Mapped[int] = mapped_column(Integer, default=0)
    last_streak_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    extra: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class PersistentContactState(Base):
    __tablename__ = "contact_states"
    __table_args__ = (
        UniqueConstraint("owner_id", "contact_id", name="uq_owner_contact_state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    contact_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("contacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    score: Mapped[int] = mapped_column(Integer, default=50)
    last_message_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    mood: Mapped[str] = mapped_column(String(50), nullable=False, default="neutral")
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class MoodEntry(Base):
    __tablename__ = "mood_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    contact_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("contacts.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    mood: Mapped[str] = mapped_column(String(50), nullable=False)
    valence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    message_snippet: Mapped[str] = mapped_column(
        String(200), nullable=False, default=""
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class RelationshipEvent(Base):
    __tablename__ = "relationship_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("contacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    score_before: Mapped[int] = mapped_column(Integer, nullable=False)
    score_after: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class ScheduledJobModel(Base):
    __tablename__ = "scheduled_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True, index=True
    )
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    delay_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    fire_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    executed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Recurrence fields
    recurrence_type: Mapped[str | None] = mapped_column(
        String(20), nullable=True, default=None
    )
    recurrence_rule: Mapped[str | None] = mapped_column(
        String(200), nullable=True, default=None
    )
    next_fire_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_executed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_response: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)


# ---------------------------------------------------------------------------
# Nudge cooldown persistence
# ---------------------------------------------------------------------------


class NudgeCooldown(Base):
    """Persists the last-fired timestamp for each owner/skill pair.

    Replaces the in-memory ``_cooldowns`` dict so nudge debouncing survives
    bot restarts.
    """

    __tablename__ = "nudge_cooldowns"
    __table_args__ = (
        UniqueConstraint("owner_id", "skill_name", name="uq_owner_skill_cooldown"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    skill_name: Mapped[str] = mapped_column(String(200), nullable=False)
    last_fired_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


# ---------------------------------------------------------------------------
# Memory-system models
# ---------------------------------------------------------------------------

OWNER_MEMORY_DEFAULT_IMPORTANCE = 8


class SemanticMemory(Base):
    __tablename__ = "semantic_memories"
    __table_args__ = (
        Index(
            "ix_semantic_memories_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    contact_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding = mapped_column(Vector(EMBED_DIMENSIONS), nullable=False)
    importance: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=5)
    category: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="general",
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default="{}",
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    expires_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    content_search = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', content)", persisted=True),
        nullable=True,
    )


# ---------------------------------------------------------------------------
# Application-wide error / warning log
# ---------------------------------------------------------------------------


class AppLog(Base):
    """Persists WARNING-level-and-above log records from the entire application.

    Populated automatically by ``DatabaseLogHandler`` (see
    ``src.core.db.log_handler``), which is attached to the root logger at
    startup.  Captures the Python logging level, logger name, formatted
    message, optional traceback, and source-code location so that warnings
    and errors can be queried, audited, and correlated after the fact.
    """

    __tablename__ = "app_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    logger_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    traceback: Mapped[str | None] = mapped_column(Text, nullable=True)
    module: Mapped[str | None] = mapped_column(String(255), nullable=True)
    func_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    line_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class OwnerMemory(Base):
    __tablename__ = "owner_memories"
    __table_args__ = (
        Index(
            "ix_owner_memories_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding = mapped_column(Vector(EMBED_DIMENSIONS), nullable=False)
    importance: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=OWNER_MEMORY_DEFAULT_IMPORTANCE,
    )
    category: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="fact",
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default="{}",
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    content_search = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', content)", persisted=True),
        nullable=True,
    )
