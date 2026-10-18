"""add message sync keys and cursors

Gives every newly ingested message a provider idempotency key and stores
a per-chat sync watermark so history backfill can resume.

Revision ID: b2c4d6e8f0a1
Revises: f1a4b6c8d2e7
Create Date: 2026-10-18 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b2c4d6e8f0a1"
down_revision: str | Sequence[str] | None = "f1a4b6c8d2e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add provider identity columns, lookup indexes, and sync_cursors."""
    op.add_column(
        "messages",
        sa.Column(
            "is_from_owner",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "messages",
        sa.Column("external_chat_id", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("external_message_id", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )
    op.create_index(
        "uq_messages_provider_key",
        "messages",
        ["platform", "external_chat_id", "external_message_id"],
        unique=True,
        postgresql_where=sa.text("external_message_id IS NOT NULL"),
    )
    op.create_index(
        "ix_messages_chat_timestamp",
        "messages",
        ["chat_id", "timestamp"],
    )
    op.create_index(
        "ix_messages_contact_timestamp",
        "messages",
        ["from_contact_id", "timestamp"],
    )
    op.create_table(
        "sync_cursors",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("platform", sa.String(length=50), nullable=False),
        sa.Column("scope", sa.String(length=512), nullable=False),
        sa.Column("newest_external_id", sa.String(length=512), nullable=True),
        sa.Column("newest_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("oldest_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "backfill_complete",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("platform", "scope", name="uq_sync_cursor_scope"),
    )


def downgrade() -> None:
    """Remove sync cursors and provider identity columns."""
    op.drop_table("sync_cursors")
    op.drop_index("ix_messages_contact_timestamp", table_name="messages")
    op.drop_index("ix_messages_chat_timestamp", table_name="messages")
    op.drop_index("uq_messages_provider_key", table_name="messages")
    op.drop_column("messages", "ingested_at")
    op.drop_column("messages", "edited_at")
    op.drop_column("messages", "external_message_id")
    op.drop_column("messages", "external_chat_id")
    op.drop_column("messages", "is_from_owner")
