"""Dynamic relationship scoring based on multi-signal analysis.

Computes a 0-100 score from four weighted signals:
- **Frequency** — message volume over the last 30 days.
- **Recency** — how recently the last message was sent.
- **Sentiment** — average mood valence from ``mood_entries``.
- **Reply rate** — ratio of owner-to-contact messages.
"""

from __future__ import annotations

import datetime
import logging
import math

from sqlalchemy import func, select

from src.core.config import settings

logger = logging.getLogger(__name__)

_WINDOW_DAYS = 30


class RelationshipManager:
    """Compute and persist multi-signal relationship scores."""

    async def calculate_score(
        self,
        contact_id: int,
        owner_id: int,
    ) -> int:
        """Calculate and persist the relationship score for a contact."""
        freq = await self._frequency_score(contact_id, owner_id)
        rec = await self._recency_score(contact_id)
        sent = await self._sentiment_score(contact_id, owner_id)
        reply = await self._reply_rate_score(contact_id, owner_id)

        w = settings
        raw = (
            w.rel_weight_frequency * freq
            + w.rel_weight_recency * rec
            + w.rel_weight_sentiment * sent
            + w.rel_weight_reply_rate * reply
        )
        score = max(0, min(100, int(round(raw))))

        await self._persist(contact_id, score)

        logger.info(
            "Relationship score for contact %d: %d "
            "(freq=%.0f rec=%.0f sent=%.0f reply=%.0f)",
            contact_id,
            score,
            freq,
            rec,
            sent,
            reply,
        )
        return score

    # ------------------------------------------------------------------
    # Signal helpers (each returns 0-100)
    # ------------------------------------------------------------------

    async def _frequency_score(self, contact_id: int, owner_id: int) -> float:
        """Score based on message count in the last 30 days (0-100)."""
        from src.core.db.models import Message
        from src.core.db.session import get_session

        cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
            days=_WINDOW_DAYS
        )

        async with get_session() as session:
            result = await session.execute(
                select(func.count())
                .select_from(Message)
                .where(
                    Message.from_contact_id == contact_id,
                    Message.is_from_owner.is_(False),
                    Message.timestamp >= cutoff,
                )
            )
            count = result.scalar_one() or 0

        if count == 0:
            return 0.0
        return min(100.0, 20 * math.log2(count + 1))

    async def _recency_score(self, contact_id: int) -> float:
        """Score based on time since last message (0-100)."""
        from src.core.db.models import Message
        from src.core.db.session import get_session

        async with get_session() as session:
            result = await session.execute(
                select(func.max(Message.timestamp)).where(
                    Message.from_contact_id == contact_id,
                    Message.is_from_owner.is_(False),
                )
            )
            last_ts = result.scalar_one_or_none()

        if last_ts is None:
            return 0.0

        now = datetime.datetime.now(datetime.UTC)
        hours_ago = (now - last_ts).total_seconds() / 3600

        return max(0.0, min(100.0, 100 * math.exp(-hours_ago / 72)))

    async def _sentiment_score(self, contact_id: int, owner_id: int) -> float:
        """Score from average valence of recent mood entries (0-100)."""
        from src.core.db.models import MoodEntry
        from src.core.db.session import get_session

        cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
            days=_WINDOW_DAYS
        )

        async with get_session() as session:
            result = await session.execute(
                select(func.avg(MoodEntry.valence)).where(
                    MoodEntry.owner_id == owner_id,
                    MoodEntry.contact_id == contact_id,
                    MoodEntry.created_at >= cutoff,
                )
            )
            avg_val = result.scalar_one_or_none()

        if avg_val is None:
            return 50.0

        return max(0.0, min(100.0, (float(avg_val) + 1) * 50))

    async def _reply_rate_score(self, contact_id: int, owner_id: int) -> float:
        """Score from reply ratio (0-100)."""
        from src.core.db.models import Message
        from src.core.db.session import get_session

        cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
            days=_WINDOW_DAYS
        )

        async with get_session() as session:
            r1 = await session.execute(
                select(func.count())
                .select_from(Message)
                .where(
                    Message.from_contact_id == contact_id,
                    Message.is_from_owner.is_(False),
                    Message.timestamp >= cutoff,
                )
            )
            contact_msgs = r1.scalar_one() or 0

            # Owner replies inside this contact's thread, not the owner's
            # private chat with the bot. ``owner_id`` is unused: the thread
            # is identified by the contact row.
            _ = owner_id
            r2 = await session.execute(
                select(func.count())
                .select_from(Message)
                .where(
                    Message.from_contact_id == contact_id,
                    Message.is_from_owner.is_(True),
                    Message.timestamp >= cutoff,
                )
            )
            owner_msgs = r2.scalar_one() or 0

        total = contact_msgs + owner_msgs
        if total == 0:
            return 0.0

        ratio = (
            min(contact_msgs, owner_msgs) / max(contact_msgs, owner_msgs)
            if max(contact_msgs, owner_msgs) > 0
            else 0
        )
        return 20 + 60 * ratio

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def _persist(self, contact_id: int, new_score: int) -> None:
        """Upsert the relationship row and log a score-change event."""
        from src.core.db.crud import get_relationship, upsert_relationship
        from src.core.db.models import RelationshipEvent
        from src.core.db.session import get_session

        old_rel = await get_relationship(contact_id)
        old_score = old_rel.score if old_rel else 50

        await upsert_relationship(contact_id, new_score)

        if old_score != new_score:
            try:
                event = RelationshipEvent(
                    contact_id=contact_id,
                    score_before=old_score,
                    score_after=new_score,
                    reason="auto-recalculation",
                )
                async with get_session() as session:
                    session.add(event)
                    await session.commit()
            except Exception:
                logger.warning("Failed to log relationship event", exc_info=True)


# Module-level singleton
relationship_manager = RelationshipManager()
