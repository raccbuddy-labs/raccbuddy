"""Advanced four-layer memory system with owner self-memory.

Architecture overview
---------------------
RaccBuddy's memory is organised into four complementary layers, all
stored in PostgreSQL (with pgvector for semantic retrieval):

1. **Episodic** — raw messages (``messages`` table).
2. **Semantic long-term** — distilled facts per contact
   (``semantic_memories``).
3. **Structured relationship** — scores & metadata on ``contacts`` /
   ``relationships``.
4. **Owner self-memory** — Raccy's personal knowledge about the owner
   (``owner_memories``).

Token efficiency
~~~~~~~~~~~~~~~~
Every retrieval path respects ``settings.max_context_tokens``
(default 30 000).  Budget fractions and retrieval limits are all
driven by ``settings.memory_*`` config keys.

Context assembly is delegated to ``ContextBuilder`` in
``src/core/memory/context_builder.py`` for clean separation of concerns.

Privacy
~~~~~~~
All data stays in the local PostgreSQL instance.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, desc, func, select, text

from src.core.config import settings
from src.core.db.crud import (
    get_all_contacts_all_platforms,
    get_contact_name,
    get_recent_messages,
    get_recent_messages_for_contact,
    get_relevant_summaries,
)
from src.core.db.models import Base, Message, OwnerMemory, SemanticMemory
from src.core.db.session import get_session
from src.core.llm.interface import embed
from src.core.state.persistent import get_contact_state, get_state

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Approximate characters per token — used to convert token budgets to char
# limits for safe string slicing without a full tokeniser.
CHARS_PER_TOKEN: int = 4

OWNER_MEMORY_DEFAULT_IMPORTANCE: int = 8
OWNER_MEMORY_PRUNE_FLOOR: int = 7


def _context_budget_chars(max_tokens: int | None = None) -> int:
    """Return the character budget for the full context window.

    Computed dynamically from ``settings.max_context_tokens`` so changes
    to the config are reflected without a restart.
    """
    return (max_tokens or settings.max_context_tokens) * CHARS_PER_TOKEN


# ---------------------------------------------------------------------------
# Lightweight document wrapper returned by hybrid_search
# ---------------------------------------------------------------------------


@dataclass
class Document:
    """A single memory document returned by search."""

    content: str
    score: float = 0.0
    importance: int = 5
    source: str = "unknown"
    created_at: datetime.datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# PostgresMemory — main memory interface
# ---------------------------------------------------------------------------


class PostgresMemory:
    """Production-grade, async, four-layer memory system."""

    async def setup(self) -> None:
        """Create tables required by the memory system."""
        from src.core.db.session import _get_engine

        async with _get_engine().begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)

        logger.info("Memory system initialised (pgvector + 4-layer tables)")

    # ------------------------------------------------------------------
    # 1. Episodic layer
    # ------------------------------------------------------------------

    async def add_message(
        self,
        *,
        platform: str,
        chat_id: int,
        from_contact_id: int,
        text_content: str,
        timestamp: datetime.datetime | None = None,
        importance: int = 3,
    ) -> Message:
        """Persist a message and optionally embed high-importance ones."""
        from src.core.db.crud import save_message

        msg = await save_message(
            platform=platform,
            chat_id=chat_id,
            from_contact_id=from_contact_id,
            text_content=text_content,
            timestamp=timestamp,
        )

        if importance >= 6:
            try:
                emb = await embed(text_content[:512])
                async with get_session() as session:
                    await session.execute(
                        text(
                            "UPDATE messages SET embedding = :emb, "
                            "importance = :imp WHERE id = :mid"
                        ),
                        {"emb": str(emb), "imp": importance, "mid": msg.id},
                    )
                    await session.commit()
            except Exception:
                logger.warning("Failed to embed message %d", msg.id)

        return msg

    # ------------------------------------------------------------------
    # 2. Semantic long-term memory (contact-scoped)
    # ------------------------------------------------------------------

    async def add_semantic_memory(
        self,
        owner_id: int,
        content: str,
        *,
        contact_id: int | None = None,
        importance: int = 5,
        category: str = "general",
        metadata: dict[str, Any] | None = None,
        embedding: list[float] | None = None,
    ) -> SemanticMemory:
        """Store a semantic memory with embedding.

        Pass ``embedding`` when the caller already computed one (history
        catch-up does this) so a backfill does not embed the same text twice.
        """
        emb = embedding if embedding is not None else await embed(content[:512])
        mem = SemanticMemory(
            owner_id=owner_id,
            contact_id=contact_id,
            content=content,
            embedding=emb,
            importance=importance,
            category=category,
            metadata_=metadata or {},
        )
        async with get_session() as session:
            session.add(mem)
            await session.commit()
            await session.refresh(mem)
        logger.debug(
            "Saved semantic memory (contact=%s, imp=%d): %.60s…",
            contact_id,
            importance,
            content,
        )
        return mem

    # ------------------------------------------------------------------
    # 3. Owner self-memory
    # ------------------------------------------------------------------

    async def add_owner_memory(
        self,
        owner_id: int,
        content: str,
        *,
        importance: int = OWNER_MEMORY_DEFAULT_IMPORTANCE,
        category: str = "fact",
        metadata: dict[str, Any] | None = None,
    ) -> OwnerMemory:
        """Store a fact about the owner with deduplication."""
        emb = await embed(content[:512])

        try:
            existing = await self._find_duplicate_owner_memory(owner_id, emb)
            if existing is not None:
                return await self._merge_owner_memory(
                    existing, content, importance, metadata
                )
        except Exception:
            logger.warning(
                "Owner memory dedup check failed; inserting as new", exc_info=True
            )

        mem = OwnerMemory(
            owner_id=owner_id,
            content=content,
            embedding=emb,
            importance=importance,
            category=category,
            metadata_=metadata or {},
        )
        async with get_session() as session:
            session.add(mem)
            await session.commit()
            await session.refresh(mem)
        logger.info(
            "Owner memory saved (cat=%s, imp=%d): %.80s…",
            category,
            importance,
            content,
        )
        return mem

    async def _find_duplicate_owner_memory(
        self,
        owner_id: int,
        embedding: list[float],
        threshold: float = 0.9,
    ) -> OwnerMemory | None:
        async with get_session() as session:
            stmt = (
                select(
                    OwnerMemory,
                    (1 - OwnerMemory.embedding.cosine_distance(embedding)).label("sim"),
                )
                .where(OwnerMemory.owner_id == owner_id)
                .order_by(desc(text("sim")))
                .limit(1)
            )
            row = (await session.execute(stmt)).first()
            if row and row.sim >= threshold:
                logger.debug(
                    "Found duplicate owner memory (sim=%.3f): %.60s…",
                    row.sim,
                    row.OwnerMemory.content,
                )
                return row.OwnerMemory
        return None

    async def _merge_owner_memory(
        self,
        existing: OwnerMemory,
        new_content: str,
        new_importance: int,
        new_metadata: dict[str, Any] | None,
    ) -> OwnerMemory:
        import datetime as _dt

        merged_importance = max(existing.importance, new_importance)

        if new_content.lower().strip() != existing.content.lower().strip():
            merged_content = f"{existing.content}; {new_content}"
            if len(merged_content) > 500:
                merged_content = merged_content[:497] + "…"
        else:
            merged_content = existing.content

        async with get_session() as session:
            merged = await session.get(OwnerMemory, existing.id)
            if merged:
                merged.content = merged_content
                merged.importance = merged_importance
                merged.updated_at = _dt.datetime.now(_dt.UTC)
                if new_metadata:
                    merged.metadata_ = {**(merged.metadata_ or {}), **new_metadata}
                merged.embedding = await embed(merged_content[:512])
                await session.commit()
                await session.refresh(merged)
                logger.info(
                    "Owner memory merged (id=%d, imp=%d): %.80s…",
                    merged.id,
                    merged_importance,
                    merged_content,
                )
                return merged

        return existing

    # ------------------------------------------------------------------
    # 4. Hybrid search (vector + full-text + filters)
    # ------------------------------------------------------------------

    async def hybrid_search(
        self,
        query: str,
        owner_id: int,
        *,
        contact_id: int | None = None,
        k: int = 10,
        min_importance: int = 3,
        include_owner_memories: bool = True,
    ) -> list[Document]:
        """Combined vector + full-text search across memory layers."""
        query_emb = await embed(query[:512])
        documents: list[Document] = []

        async with get_session() as session:
            # --- Semantic memories (contact-scoped) ---
            vec_score_expr = (
                1 - SemanticMemory.embedding.cosine_distance(query_emb)
            ).label("vec_score")
            fts_score_expr = func.ts_rank(
                SemanticMemory.content_search,
                func.plainto_tsquery("english", query),
            ).label("fts_score")
            combined_score = (0.7 * vec_score_expr + 0.3 * fts_score_expr).label(
                "combined_score"
            )

            sem_stmt = select(
                SemanticMemory.content,
                SemanticMemory.importance,
                SemanticMemory.category,
                SemanticMemory.created_at,
                SemanticMemory.metadata_,
                vec_score_expr,
                fts_score_expr,
                combined_score,
            ).where(
                SemanticMemory.owner_id == owner_id,
                SemanticMemory.importance >= min_importance,
            )
            if contact_id is not None:
                sem_stmt = sem_stmt.where(
                    SemanticMemory.contact_id == contact_id,
                )

            sem_stmt = sem_stmt.order_by(desc(combined_score)).limit(k)

            try:
                sem_rows = (await session.execute(sem_stmt)).all()
                for row in sem_rows:
                    documents.append(
                        Document(
                            content=row.content,
                            score=0.7 * (row.vec_score or 0)
                            + 0.3 * (row.fts_score or 0),
                            importance=row.importance,
                            source="semantic",
                            created_at=row.created_at,
                            metadata=row.metadata_ or {},
                        )
                    )
            except Exception:
                logger.warning("Semantic hybrid search failed", exc_info=True)

            # --- Owner memories ---
            if include_owner_memories:
                own_vec_score = (
                    1 - OwnerMemory.embedding.cosine_distance(query_emb)
                ).label("vec_score")
                own_fts_score = func.ts_rank(
                    OwnerMemory.content_search,
                    func.plainto_tsquery("english", query),
                ).label("fts_score")
                own_combined_score = (0.7 * own_vec_score + 0.3 * own_fts_score).label(
                    "combined_score"
                )

                own_stmt = (
                    select(
                        OwnerMemory.content,
                        OwnerMemory.importance,
                        OwnerMemory.category,
                        OwnerMemory.created_at,
                        OwnerMemory.metadata_,
                        own_vec_score,
                        own_fts_score,
                        own_combined_score,
                    )
                    .where(
                        OwnerMemory.owner_id == owner_id,
                        OwnerMemory.importance >= min_importance,
                    )
                    .order_by(desc(own_combined_score))
                    .limit(k)
                )
                try:
                    own_rows = (await session.execute(own_stmt)).all()
                    for row in own_rows:
                        documents.append(
                            Document(
                                content=row.content,
                                score=0.7 * (row.vec_score or 0)
                                + 0.3 * (row.fts_score or 0),
                                importance=row.importance,
                                source="owner_memory",
                                created_at=row.created_at,
                                metadata=row.metadata_ or {},
                            )
                        )
                except Exception:
                    logger.warning("Owner memory hybrid search failed", exc_info=True)

        documents.sort(key=lambda d: d.score, reverse=True)
        return documents[:k]

    # ------------------------------------------------------------------
    # Owner personal facts — formatted for prompt injection
    # ------------------------------------------------------------------

    async def get_owner_personal_facts(
        self,
        owner_id: int,
        *,
        query: str | None = None,
        k: int = 8,
    ) -> str:
        """Return formatted owner facts for prompt injection.

        When *query* is provided, facts are ranked by semantic similarity
        to the query so only relevant knowledge is surfaced. Without a
        query, falls back to importance + recency ordering.
        """
        budget = int(_context_budget_chars() * settings.memory_owner_budget_ratio)

        async with get_session() as session:
            if query:
                try:
                    query_emb = await embed(query[:512])
                    relevance = (
                        1 - OwnerMemory.embedding.cosine_distance(query_emb)
                    ).label("relevance")
                    stmt = (
                        select(
                            OwnerMemory.content,
                            OwnerMemory.category,
                            OwnerMemory.importance,
                            relevance,
                        )
                        .where(OwnerMemory.owner_id == owner_id)
                        .order_by(desc(relevance))
                        .limit(k)
                    )
                except Exception:
                    logger.warning("Owner facts vector search failed, falling back")
                    query = None  # fall through to importance ordering

            if not query:
                stmt = (
                    select(
                        OwnerMemory.content,
                        OwnerMemory.category,
                        OwnerMemory.importance,
                    )
                    .where(OwnerMemory.owner_id == owner_id)
                    .order_by(
                        desc(OwnerMemory.importance), desc(OwnerMemory.created_at)
                    )
                    .limit(k)
                )

            rows = (await session.execute(stmt)).all()

        if not rows:
            return ""

        lines = [
            "[Background knowledge about you — use only if relevant to the question]"
        ]
        used = len(lines[0])
        for row in rows:
            line = f"- ({row.category}) {row.content}"
            if used + len(line) + 1 > budget:
                break
            lines.append(line)
            used += len(line) + 1

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Context builder
    # ------------------------------------------------------------------

    async def get_relevant_context(
        self,
        owner_id: int,
        contact_id: int | None,
        query: str,
        *,
        max_tokens: int | None = None,
    ) -> str:
        """Build a token-budgeted context string for the LLM prompt."""
        budget_chars = _context_budget_chars(max_tokens)
        parts: list[str] = []

        # 1. Owner self-memory (query-aware retrieval)
        owner_facts = await self.get_owner_personal_facts(owner_id, query=query)
        if owner_facts:
            parts.append(owner_facts)

        # 2. State snapshot
        user_state = get_state(owner_id)
        parts.append(
            f"[User state: mood={user_state.mood}, "
            f"msgs_today={user_state.message_count_today}, "
            f"streak={user_state.streak_days}d]"
        )

        if contact_id is not None:
            contact_state = get_contact_state(owner_id, contact_id)
            name = await get_contact_name(contact_id) or str(contact_id)
            parts.append(
                f"[Contact: {name}, "
                f"score={contact_state.score}/100, "
                f"mood={contact_state.mood}]"
            )

        contacts = await get_all_contacts_all_platforms(owner_id)
        if contacts:
            names = ", ".join(c.contact_name for c in contacts[:10])
            parts.append(f"[Known contacts (reference only): {names}]")

        # 3. Semantic memories (hybrid search)
        contact_budget = int(budget_chars * settings.memory_contact_budget_ratio)
        if contact_id is not None:
            try:
                docs = await self.hybrid_search(
                    query,
                    owner_id,
                    contact_id=contact_id,
                    k=settings.memory_semantic_chunks,
                    include_owner_memories=False,
                )
                if docs:
                    mem_lines = ["[Relevant memories — reference only when pertinent]"]
                    used = 0
                    for doc in docs:
                        line = f"- {doc.content[:300]}"
                        if used + len(line) > contact_budget:
                            break
                        mem_lines.append(line)
                        used += len(line)
                    parts.append("\n".join(mem_lines))
            except Exception:
                logger.warning("Semantic memory retrieval failed")

        # 4. Relevant summaries (vector search)
        if contact_id is not None:
            try:
                query_embedding = await embed(query[:512])
                summaries = await get_relevant_summaries(
                    contact_id,
                    query_embedding,
                    limit=settings.memory_max_summaries,
                )
                if summaries:
                    parts.append("[Relevant history]")
                    for s in summaries:
                        parts.append(f"- {s.date}: {s.summary_text[:300]}")
            except Exception:
                logger.warning("Summary retrieval failed for contact %d", contact_id)

        # 5. Recent episodic messages
        if contact_id is not None:
            messages = await get_recent_messages_for_contact(
                contact_id,
                limit=settings.memory_recent_messages,
            )
        else:
            messages = await get_recent_messages(
                owner_id,
                limit=settings.memory_recent_messages,
            )

        if messages:
            parts.append("[Recent messages]")
            for m in messages:
                parts.append(f"- {m.text[:200]}")

        # 6. Current message
        parts.append(f"[Current message]: {query}")

        context = "\n".join(parts)

        if len(context) > budget_chars:
            context = context[:budget_chars]
            logger.warning("Context truncated to %d chars", budget_chars)

        return context

    # ------------------------------------------------------------------
    # Pruning
    # ------------------------------------------------------------------

    async def prune_old_memories(self, *, days: int = 90) -> int:
        """Delete low-importance memories older than *days*."""
        cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)
        deleted = 0

        async with get_session() as session:
            sem_del = delete(SemanticMemory).where(
                SemanticMemory.importance < 5,
                SemanticMemory.created_at < cutoff,
            )
            r1 = await session.execute(sem_del)
            deleted += r1.rowcount

            own_del = delete(OwnerMemory).where(
                OwnerMemory.importance < OWNER_MEMORY_PRUNE_FLOOR,
                OwnerMemory.created_at < cutoff,
            )
            r2 = await session.execute(own_del)
            deleted += r2.rowcount

            await session.commit()

        if deleted:
            logger.info(
                "Pruned %d old memories (cutoff=%s, owner floor=%d)",
                deleted,
                cutoff.date(),
                OWNER_MEMORY_PRUNE_FLOOR,
            )
        return deleted

    # ------------------------------------------------------------------
    # Consolidation / Reflection
    # ------------------------------------------------------------------

    async def consolidate_memories(
        self,
        owner_id: int,
        *,
        recent_hours: int = 24,
        max_input_chars: int = 2000,
    ) -> str | None:
        """Run daily reflection to extract lasting owner facts."""
        from src.core.llm.interface import generate as llm_generate

        cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
            hours=recent_hours
        )

        async with get_session() as session:
            stmt = (
                select(SemanticMemory.content)
                .where(
                    SemanticMemory.owner_id == owner_id,
                    SemanticMemory.created_at >= cutoff,
                )
                .order_by(desc(SemanticMemory.created_at))
                .limit(20)
            )
            rows = (await session.execute(stmt)).scalars().all()

        if not rows:
            return None

        block = "\n".join(f"- {r[:150]}" for r in rows)[:max_input_chars]

        reflection_prompt = (
            "You are Raccy 🦝, the user's AI raccoon companion.\n"
            "Below are recent memories and observations from today's conversations.\n"
            "Extract 1–5 NEW lasting facts you learned about your human.\n"
            "Focus on: preferences, personality traits, emotional patterns, "
            "goals, routines, inside jokes, important dates.\n"
            "Output each fact on its own line, prefixed with a category tag "
            "in parentheses, e.g.:\n"
            "(preference) Loves iced oat lattes\n"
            "(trait) Tends to overthink decisions\n\n"
            f"Recent memories:\n{block}"
        )

        reflection = await llm_generate(
            reflection_prompt,
            system="You are a memory extraction assistant. Output only the facts.",
        )

        saved = 0
        for line in reflection.strip().splitlines():
            line = line.strip().lstrip("- ").strip()
            if not line or len(line) < 5:
                continue

            category = "fact"
            if line.startswith("(") and ")" in line:
                tag_end = line.index(")")
                tag = line[1:tag_end].lower().strip()
                allowed = {
                    "preference",
                    "trait",
                    "joke",
                    "emotion",
                    "reflection",
                    "fact",
                    "goal",
                    "routine",
                    "boundary",
                }
                if tag in allowed:
                    category = tag
                line = line[tag_end + 1 :].strip()

            if line:
                await self.add_owner_memory(
                    owner_id,
                    line,
                    category=category,
                    metadata={"source": "reflection", "window_hours": recent_hours},
                )
                saved += 1

        logger.info("Consolidation produced %d owner memories", saved)
        return reflection


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

memory = PostgresMemory()


# ---------------------------------------------------------------------------
# Backward-compatible function
# ---------------------------------------------------------------------------


async def build_context_for_contact(
    owner_id: int,
    contact_id: int,
    current_message: str,
) -> str:
    """Convenience wrapper around ``memory.get_relevant_context``."""
    return await memory.get_relevant_context(
        owner_id,
        contact_id,
        current_message,
    )
