"""Daily conversation summarization.

Generates per-contact daily summaries (max 150 words) and stores them
with pgvector embeddings for semantic retrieval.
"""

from __future__ import annotations

import datetime
import logging

from src.core.config import settings
from src.core.db.crud import (
    get_contacts_with_messages_since,
    get_messages_since,
    get_summary_for_date,
    save_summary,
)
from src.core.llm import embed, generate

logger = logging.getLogger(__name__)

SUMMARY_SYSTEM_PROMPT = (
    "You are a concise conversation summarizer. "
    "Output only the summary, no preamble."
)


async def summarize_daily(contact_id: int) -> str | None:
    """Generate a daily summary for a contact.

    Args:
        contact_id: The contact's database ID (contacts.id).

    Returns:
        The summary text, or None if already exists or no messages found.
    """
    today = datetime.date.today()

    # Skip if summary already exists for today
    if await get_summary_for_date(contact_id, today):
        logger.info(
            "Summary already exists for contact %d on %s",
            contact_id,
            today,
        )
        return None

    # Fetch today's messages attributed to this contact
    day_start = datetime.datetime.combine(
        today,
        datetime.time.min,
        tzinfo=datetime.UTC,
    )
    msgs = await get_messages_since(
        from_contact_id=contact_id,
        since=day_start,
    )

    if not msgs:
        return None

    # Cap input to avoid token bloat (max 20 messages, 100 chars each)
    raw_text = "\n".join(f"- {m.text[:100]}" for m in msgs[:20])
    max_words = settings.max_summary_words

    prompt = (
        f"Summarize this conversation in under {max_words} words. "
        f"Focus on key topics, emotions, and action items:\n{raw_text}"
    )

    summary_text = await generate(prompt, system=SUMMARY_SYSTEM_PROMPT)

    # Generate embedding and persist
    embedding = await embed(summary_text)

    await save_summary(
        contact_id=contact_id,
        date=today,
        summary_text=summary_text,
        embedding=embedding,
    )

    logger.info("Created daily summary for contact %d", contact_id)
    return summary_text


async def summarize_all_contacts(platform: str | None = None) -> list[str]:
    """Scan all contacts with today's messages and generate summaries.

    Also stores each summary as a semantic memory for richer retrieval.

    Args:
        platform: Optional platform filter (None = all platforms).

    Returns:
        List of summary texts generated.
    """
    from src.core.db.crud import get_contact_by_id
    from src.core.memory import memory

    today = datetime.date.today()
    day_start = datetime.datetime.combine(
        today,
        datetime.time.min,
        tzinfo=datetime.UTC,
    )

    contact_ids = await get_contacts_with_messages_since(day_start, platform)

    summaries: list[str] = []
    for cid in contact_ids:
        text = await summarize_daily(cid)
        if text:
            summaries.append(text)
            # Store as semantic memory for hybrid search
            try:
                contact = await get_contact_by_id(cid)
                owner_id = contact.owner_id if contact else 0
                if owner_id:
                    await memory.add_semantic_memory(
                        owner_id,
                        text,
                        contact_id=cid,
                        importance=5,
                        category="summary",
                        metadata={"date": str(today)},
                    )
            except Exception:
                logger.warning(
                    "Failed to store semantic memory for contact %d",
                    cid,
                )

    logger.info("Summarized %d/%d contacts", len(summaries), len(contact_ids))

    # Drain a bounded slice of historical days left behind by a backfill.
    # This is separate from today's summary so a large import cannot turn
    # the 6-hour job into one LLM call per raw message.
    try:
        from src.core.sync.catchup import catchup_unsummarized_days

        await catchup_unsummarized_days()
    except Exception:
        logger.warning("Historical summary catch-up failed", exc_info=True)

    return summaries
