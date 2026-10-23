"""add HNSW indexes on pgvector embedding columns

Cosine search on ``semantic_memories``, ``owner_memories``, and
``summaries`` was a sequential scan. HNSW with ``vector_cosine_ops``
matches the ``cosine_distance`` queries already in the memory layer.
Dimensions stay 768. Summaries are partial because that column is nullable.

Requires pgvector >= 0.5 (the ``pgvector/pgvector:pg16`` image provides it).
The build takes a share lock for the duration of the index build, which is
short on a single-owner archive.

Revision ID: d4e6f8a0b2c3
Revises: c3d5e7f9a1b2
Create Date: 2026-10-23 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "d4e6f8a0b2c3"
down_revision: str | Sequence[str] | None = "c3d5e7f9a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES = (
    (
        "ix_semantic_memories_embedding_hnsw",
        (
            "CREATE INDEX IF NOT EXISTS ix_semantic_memories_embedding_hnsw "
            "ON semantic_memories USING hnsw (embedding vector_cosine_ops)"
        ),
    ),
    (
        "ix_owner_memories_embedding_hnsw",
        (
            "CREATE INDEX IF NOT EXISTS ix_owner_memories_embedding_hnsw "
            "ON owner_memories USING hnsw (embedding vector_cosine_ops)"
        ),
    ),
    (
        "ix_summaries_embedding_hnsw",
        (
            "CREATE INDEX IF NOT EXISTS ix_summaries_embedding_hnsw "
            "ON summaries USING hnsw (embedding vector_cosine_ops) "
            "WHERE embedding IS NOT NULL"
        ),
    ),
)


def upgrade() -> None:
    """Create cosine HNSW indexes. Existing vectors are not rewritten."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    for _name, statement in _INDEXES:
        op.execute(statement)


def downgrade() -> None:
    """Drop the HNSW indexes. Vectors themselves stay."""
    for name, _statement in reversed(_INDEXES):
        op.execute(f"DROP INDEX IF EXISTS {name}")
