"""Standardized CRUD operations for all domain models."""

from __future__ import annotations

import datetime
import logging
from collections.abc import Sequence
from typing import Any

from sqlalchemy import desc, distinct, func, nullslast, select

from src.core.db.models import Contact, Habit, Message, Relationship, Summary
from src.core.db.session import get_session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CRUD helpers — Messages
# ---------------------------------------------------------------------------


async def save_message(
    *,
    platform: str,
    chat_id: int,
    from_contact_id: int | None = None,
    text_content: str,
    is_bot_reply: bool = False,
    timestamp: datetime.datetime | None = None,
) -> Message:
    """Persist a new chat message."""
    msg = Message(
        platform=platform,
        chat_id=chat_id,
        from_contact_id=from_contact_id,
        text=text_content,
        is_bot_reply=is_bot_reply,
        **({"timestamp": timestamp} if timestamp else {}),
    )
    async with get_session() as session:
        session.add(msg)
        await session.commit()
    return msg


async def get_recent_messages(
    chat_id: int,
    *,
    from_contact_id: int | None = None,
    limit: int = 5,
) -> list[Message]:
    """Return the most recent messages for a chat."""
    stmt = select(Message).where(Message.chat_id == chat_id)
    if from_contact_id is not None:
        stmt = stmt.where(Message.from_contact_id == from_contact_id)
    stmt = stmt.order_by(
        desc(Message.timestamp),
        nullslast(desc(Message.external_message_id)),
        desc(Message.id),
    ).limit(limit)

    async with get_session() as session:
        result = await session.execute(stmt)
        return list(reversed(result.scalars().all()))


async def get_conversation_history(
    chat_id: int,
    *,
    limit: int = 10,
) -> list[Message]:
    """Return the most recent owner-side conversation turns for a chat.

    Retrieves the last *limit* messages where ``from_contact_id IS NULL``
    (i.e. owner messages and bot replies), ordered chronologically
    (oldest first).

    Args:
        chat_id: The Telegram chat ID (usually the owner ID).
        limit: Maximum number of turns to return.

    Returns:
        List of Message objects in chronological order.
    """
    stmt = (
        select(Message)
        .where(
            Message.chat_id == chat_id,
            Message.from_contact_id.is_(None),
        )
        .order_by(
            desc(Message.timestamp),
            nullslast(desc(Message.external_message_id)),
            desc(Message.id),
        )
        .limit(limit)
    )
    async with get_session() as session:
        result = await session.execute(stmt)
        return list(reversed(result.scalars().all()))


async def get_messages_since(
    *,
    from_contact_id: int,
    since: datetime.datetime,
    limit: int | None = 20,
) -> list[Message]:
    """Return messages from a contact since a given timestamp.

    ``limit=None`` returns the whole window. The daily summarizer needs
    that so a backfill is not clipped to the first 20 rows.
    """
    stmt = (
        select(Message)
        .where(
            Message.from_contact_id == from_contact_id,
            Message.timestamp >= since,
        )
        .order_by(
            Message.timestamp,
            nullslast(Message.external_message_id),
            Message.id,
        )
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    async with get_session() as session:
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def get_idle_contact_ids(
    cutoff: datetime.datetime,
) -> list[tuple[int, datetime.datetime]]:
    """Return contacts whose last message is before *cutoff*."""
    stmt = (
        select(Message.from_contact_id, func.max(Message.timestamp).label("last_msg"))
        .where(Message.from_contact_id.isnot(None))
        .group_by(Message.from_contact_id)
        .having(func.max(Message.timestamp) < cutoff)
    )
    async with get_session() as session:
        result = await session.execute(stmt)
        return [(int(row[0]), row[1]) for row in result.all()]


async def get_last_message_ts_for_contact(
    contact_id: int,
    chat_id: int,
) -> datetime.datetime | None:
    """Return the timestamp of the last message from a contact in a chat."""
    stmt = select(func.max(Message.timestamp)).where(
        Message.from_contact_id == contact_id,
        Message.chat_id == chat_id,
    )
    async with get_session() as session:
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# CRUD helpers — Contacts
# ---------------------------------------------------------------------------


async def upsert_contact(
    *,
    owner_id: int,
    contact_handle: str,
    platform: str = "telegram",
    contact_name: str,
) -> Contact:
    """Create or update a contact record."""
    async with get_session() as session:
        stmt = select(Contact).filter_by(
            owner_id=owner_id,
            contact_handle=contact_handle,
            platform=platform,
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            existing.contact_name = contact_name
            contact = existing
        else:
            contact = Contact(
                owner_id=owner_id,
                contact_handle=contact_handle,
                platform=platform,
                contact_name=contact_name,
            )
            session.add(contact)

        await session.commit()
        await session.refresh(contact)
    return contact


async def get_contact(
    owner_id: int,
    contact_handle: str,
    platform: str = "telegram",
) -> Contact | None:
    """Look up a contact by handle and platform."""
    async with get_session() as session:
        result = await session.execute(
            select(Contact).where(
                Contact.owner_id == owner_id,
                Contact.contact_handle == contact_handle,
                Contact.platform == platform,
            )
        )
        return result.scalar_one_or_none()


async def get_contact_by_id(
    contact_id: int,
) -> Contact | None:
    """Look up a contact by primary key."""
    async with get_session() as session:
        result = await session.execute(select(Contact).where(Contact.id == contact_id))
        return result.scalar_one_or_none()


async def get_contact_by_name(
    owner_id: int,
    name: str,
    platform: str = "telegram",
) -> Contact | None:
    """Case-insensitive contact lookup by name on a specific platform."""
    async with get_session() as session:
        result = await session.execute(
            select(Contact).where(
                Contact.owner_id == owner_id,
                Contact.contact_name.ilike(name),
                Contact.platform == platform,
            )
        )
        return result.scalar_one_or_none()


async def get_contact_name(
    contact_id: int,
) -> str | None:
    """Return just the display name for a contact."""
    async with get_session() as session:
        result = await session.execute(
            select(Contact.contact_name).where(
                Contact.id == contact_id,
            )
        )
        return result.scalar_one_or_none()


async def get_all_contacts(
    owner_id: int,
    platform: str = "telegram",
) -> list[Contact]:
    """Return all contacts for an owner on a given platform."""
    async with get_session() as session:
        result = await session.execute(
            select(Contact).where(
                Contact.owner_id == owner_id,
                Contact.platform == platform,
            )
        )
        return list(result.scalars().all())


async def get_all_owner_ids() -> list[int]:
    """Return distinct owner IDs."""
    async with get_session() as session:
        result = await session.execute(select(distinct(Contact.owner_id)))
        return [row[0] for row in result.all()]


async def get_contact_by_name_any_platform(
    owner_id: int,
    name: str,
) -> Contact | None:
    """Case-insensitive contact lookup across all platforms."""
    async with get_session() as session:
        result = await session.execute(
            select(Contact).where(
                Contact.owner_id == owner_id,
                Contact.contact_name.ilike(name),
            )
        )
        return result.scalars().first()


async def get_all_contacts_all_platforms(
    owner_id: int,
) -> list[Contact]:
    """Return all contacts for an owner regardless of platform."""
    async with get_session() as session:
        result = await session.execute(
            select(Contact).where(Contact.owner_id == owner_id)
        )
        return list(result.scalars().all())


async def get_recent_messages_for_contact(
    contact_id: int,
    *,
    limit: int = 5,
) -> list[Message]:
    """Return the most recent messages from a specific contact."""
    stmt = (
        select(Message)
        .where(Message.from_contact_id == contact_id)
        .order_by(
            desc(Message.timestamp),
            nullslast(desc(Message.external_message_id)),
            desc(Message.id),
        )
        .limit(limit)
    )
    async with get_session() as session:
        result = await session.execute(stmt)
        return list(reversed(result.scalars().all()))


# ---------------------------------------------------------------------------
# CRUD helpers — Summaries
# ---------------------------------------------------------------------------


async def save_summary(
    *,
    contact_id: int,
    date: datetime.date,
    summary_text: str,
    embedding: Sequence[float] | None = None,
) -> Summary:
    """Persist a daily summary for a contact."""
    summary = Summary(
        contact_id=contact_id,
        date=date,
        summary_text=summary_text,
        embedding=embedding,
    )
    async with get_session() as session:
        session.add(summary)
        await session.commit()
    return summary


async def get_summary_for_date(
    contact_id: int,
    date: datetime.date,
) -> Summary | None:
    """Return the summary for a specific contact and date."""
    async with get_session() as session:
        result = await session.execute(
            select(Summary).where(
                Summary.contact_id == contact_id,
                Summary.date == date,
            )
        )
        return result.scalar_one_or_none()


async def get_relevant_summaries(
    contact_id: int,
    query_embedding: Sequence[float],
    *,
    limit: int = 3,
) -> list[Summary]:
    """Return summaries ranked by cosine similarity to *query_embedding*."""
    async with get_session() as session:
        try:
            result = await session.execute(
                select(Summary)
                .where(
                    Summary.contact_id == contact_id,
                    Summary.embedding.isnot(None),
                )
                .order_by(Summary.embedding.cosine_distance(query_embedding))
                .limit(limit)
            )
            return list(result.scalars().all())
        except Exception:
            logger.warning("Vector search failed; falling back to recent summaries")
            result = await session.execute(
                select(Summary)
                .where(Summary.contact_id == contact_id)
                .order_by(desc(Summary.date))
                .limit(limit)
            )
            return list(result.scalars().all())


async def get_contacts_with_messages_since(
    since: datetime.datetime,
    platform: str | None = None,
) -> list[int]:
    """Return contact IDs that have messages since *since*."""
    stmt = select(distinct(Message.from_contact_id)).where(
        Message.timestamp >= since,
        Message.from_contact_id.isnot(None),
    )
    if platform:
        stmt = stmt.where(Message.platform == platform)

    async with get_session() as session:
        result = await session.execute(stmt)
        return [row[0] for row in result.all()]


# ---------------------------------------------------------------------------
# CRUD helpers — Relationships
# ---------------------------------------------------------------------------


async def get_relationship(contact_id: int) -> Relationship | None:
    """Return the relationship record for a contact."""
    async with get_session() as session:
        result = await session.execute(
            select(Relationship).where(
                Relationship.contact_id == contact_id,
            )
        )
        return result.scalar_one_or_none()


async def upsert_relationship(
    contact_id: int,
    score: int,
    metadata: dict[str, Any] | None = None,
) -> Relationship:
    """Create or update a relationship score."""
    score = max(0, min(100, score))
    async with get_session() as session:
        result = await session.execute(
            select(Relationship).where(Relationship.contact_id == contact_id)
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.score = score
            if metadata:
                existing.metadata_ = {**(existing.metadata_ or {}), **metadata}
            rel = existing
        else:
            rel = Relationship(
                contact_id=contact_id,
                score=score,
                metadata_=metadata or {},
            )
            session.add(rel)
        await session.commit()
        await session.refresh(rel)
    return rel


# ---------------------------------------------------------------------------
# CRUD helpers — Habits
# ---------------------------------------------------------------------------


async def get_all_habits(owner_id: int | None = None) -> list[Habit]:
    """Return persisted habits, optionally filtered by owner.

    Args:
        owner_id: If provided, return only habits belonging to this owner.
                  Pass ``None`` to return all habits (admin / testing use).

    Returns:
        List of matching ``Habit`` rows.
    """
    async with get_session() as session:
        stmt = select(Habit)
        if owner_id is not None:
            stmt = stmt.where(Habit.owner_id == owner_id)
        result = await session.execute(stmt)
        return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Nudge helpers
# ---------------------------------------------------------------------------


async def count_messages_since(
    owner_id: int,
    since: datetime.datetime,
) -> int:
    """Count messages in a chat since a timestamp."""
    stmt = (
        select(func.count())
        .select_from(Message)
        .where(
            Message.chat_id == owner_id,
            Message.timestamp >= since,
        )
    )
    async with get_session() as session:
        result = await session.execute(stmt)
        return result.scalar_one() or 0


async def count_messages_from_contact_since(
    contact_id: int,
    owner_id: int,
    since: datetime.datetime,
) -> int:
    """Count messages from a specific contact since a timestamp."""
    stmt = (
        select(func.count())
        .select_from(Message)
        .where(
            Message.from_contact_id == contact_id,
            Message.chat_id == owner_id,
            Message.timestamp >= since,
        )
    )
    async with get_session() as session:
        result = await session.execute(stmt)
        return result.scalar_one() or 0
