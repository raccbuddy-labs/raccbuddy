"""Daily conversation summarization.

Generates per-contact daily summaries (max 150 words) and stores them
with pgvector embeddings for semantic retrieval.
"""

from __future__ import annotations

import datetime
import logging

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


def _utc_today() -> datetime.date:
    """Calendar day used for summaries. Catch-up groups messages in UTC."""
    return datetime.datetime.now(datetime.UTC).date()


async def summarize_daily(contact_id: int) -> str | None:
    """Generate a daily summary for a contact.

    The whole UTC day is windowed (same path as history catch-up). A day
    that would exceed the call budget is left unsaved so the next pass can
    summarize it in full.

    Args:
        contact_id: The contact's database ID (contacts.id).

    Returns:
        The summary text, or None if already exists, empty, or deferred.
    """
    today = _utc_today()

    # Skip if summary already exists for today
    if await get_summary_for_date(contact_id, today):
        logger.info(
            "Summary already exists for contact %d on %s",
            contact_id,
            today,
        )
        return None

    # Today's messages, in send order, with no 20-row clip. A backfill
    # that landed today used to be summarized from 20 lines of 100
    # characters, and that partial summary then blocked catch-up.
    day_start = datetime.datetime.combine(
        today,
        datetime.time.min,
        tzinfo=datetime.UTC,
    )
    msgs = await get_messages_since(
        from_contact_id=contact_id,
        since=day_start,
        limit=None,
    )

    if not msgs:
        return None

    from src.core.sync.catchup import DayMessages, summarize_days

    async def _generate(prompt: str) -> str:
        return await generate(prompt, system=SUMMARY_SYSTEM_PROMPT)

    report = await summarize_days(
        [
            DayMessages(
                contact_id=contact_id,
                day=today,
                texts=[m.text for m in msgs],
            )
        ],
        generate=_generate,
        embed=embed,
    )
    if not report.drafts:
        logger.info(
            "Deferred daily summary for contact %d (%d messages)",
            contact_id,
            len(msgs),
        )
        return None

    draft = report.drafts[0]
    await save_summary(
        contact_id=contact_id,
        date=today,
        summary_text=draft.text,
        embedding=draft.embedding,
    )

    logger.info("Created daily summary for contact %d", contact_id)
    return draft.text


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

    today = _utc_today()
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
