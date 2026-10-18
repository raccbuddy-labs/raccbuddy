"""Turn a large backfill into a bounded number of summary and embed calls.

Historical messages are not classified for mood one-by-one. A calendar day
is split into windows. Each window is one generation. The day is one
embedding. Days that would exceed the remaining call budget are left for
the next run, so a multi-year import cannot pin the GPU inside one request.
"""

from __future__ import annotations

import datetime
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from src.core.config import settings

logger = logging.getLogger(__name__)

GenerateFn = Callable[[str], Awaitable[str]]
EmbedFn = Callable[[str], Awaitable[list[float]]]


@dataclass
class DayMessages:
    """Ordered message texts for one contact on one UTC day."""

    contact_id: int
    day: datetime.date
    texts: list[str]


@dataclass
class SummaryDraft:
    """A day's summary plus the single embedding for that text."""

    contact_id: int
    day: datetime.date
    text: str
    embedding: list[float]


@dataclass
class CatchupReport:
    """What one bounded catch-up pass did."""

    days_summarized: int = 0
    llm_calls: int = 0
    embed_calls: int = 0
    days_deferred: int = 0
    drafts: list[SummaryDraft] = field(default_factory=list)


def _windows(texts: list[str], window: int) -> list[list[str]]:
    size = max(1, window)
    return [texts[index : index + size] for index in range(0, len(texts), size)]


async def summarize_days(
    days: list[DayMessages],
    *,
    generate: GenerateFn,
    embed: EmbedFn,
    window: int | None = None,
    max_days: int | None = None,
    max_llm_calls: int | None = None,
    pause_seconds: float | None = None,
) -> CatchupReport:
    """Summarize whole days without one model call per message.

    A day is deferred entirely when its windows would exceed
    ``max_llm_calls``. That keeps a half-written day from being treated as
    done by the caller.
    """
    window = window if window is not None else settings.sync_summary_window
    max_days = max_days if max_days is not None else settings.sync_summary_max_days
    max_llm_calls = (
        max_llm_calls
        if max_llm_calls is not None
        else settings.sync_summary_max_llm_calls
    )
    pause_seconds = (
        pause_seconds
        if pause_seconds is not None
        else settings.sync_summary_pause_seconds
    )
    report = CatchupReport()
    import asyncio

    for bucket in days:
        if report.days_summarized >= max_days:
            report.days_deferred += 1
            continue
        if not bucket.texts:
            continue
        needed = math.ceil(len(bucket.texts) / max(1, window))
        if report.llm_calls + needed > max_llm_calls:
            report.days_deferred += 1
            continue
        parts: list[str] = []
        for chunk in _windows(bucket.texts, window):
            prompt = _prompt(bucket.day, chunk)
            parts.append(await generate(prompt))
            report.llm_calls += 1
            if pause_seconds > 0:
                await asyncio.sleep(pause_seconds)
        summary = "\n".join(part.strip() for part in parts if part.strip())
        if not summary:
            continue
        embedding = await embed(summary)
        report.embed_calls += 1
        report.days_summarized += 1
        report.drafts.append(
            SummaryDraft(
                contact_id=bucket.contact_id,
                day=bucket.day,
                text=summary,
                embedding=embedding,
            )
        )
    return report


def _prompt(day: datetime.date, texts: list[str]) -> str:
    max_words = settings.max_summary_words
    lines = "\n".join(f"- {text[:1000]}" for text in texts)
    return (
        f"Summarize this conversation from {day.isoformat()} in under "
        f"{max_words} words. Focus on key topics, emotions, and action items.\n"
        f"{lines}"
    )


async def catchup_unsummarized_days(
    *,
    max_days: int | None = None,
) -> CatchupReport:
    """Load days that have messages and no summary, then summarize a bounded set.

    Called from the periodic summary job. Failures in embedding storage for
    one day are logged and do not abort the rest of the pass.
    """
    from src.core.db.crud import get_summary_for_date, save_summary
    from src.core.llm import embed, generate
    from src.core.memory import memory

    pending = await _load_pending_days(
        limit=(max_days or settings.sync_summary_max_days) * 4
    )
    # Drop days that already have a summary (the loader is a coarse scan).
    fresh: list[DayMessages] = []
    for bucket in pending:
        existing = await get_summary_for_date(bucket.contact_id, bucket.day)
        if existing is None:
            fresh.append(bucket)

    async def _generate(prompt: str) -> str:
        return await generate(
            prompt,
            system=(
                "You are a concise conversation summarizer. "
                "Output only the summary, no preamble."
            ),
        )

    report = await summarize_days(
        fresh,
        generate=_generate,
        embed=embed,
        max_days=max_days,
    )
    for draft in report.drafts:
        try:
            if await get_summary_for_date(draft.contact_id, draft.day):
                continue
            await save_summary(
                contact_id=draft.contact_id,
                date=draft.day,
                summary_text=draft.text,
                embedding=draft.embedding,
            )
            from src.core.db.crud import get_contact_by_id

            contact = await get_contact_by_id(draft.contact_id)
            owner_id = contact.owner_id if contact else 0
            if owner_id:
                await memory.add_semantic_memory(
                    owner_id,
                    draft.text,
                    contact_id=draft.contact_id,
                    importance=5,
                    category="summary",
                    metadata={"date": draft.day.isoformat(), "source": "backfill"},
                    embedding=draft.embedding,
                )
        except Exception:
            logger.warning(
                "Failed to store catch-up summary for contact %s on %s",
                draft.contact_id,
                draft.day,
                exc_info=True,
            )
    logger.info(
        "History catch-up summarized %d day(s), deferred %d, llm_calls=%d",
        report.days_summarized,
        report.days_deferred,
        report.llm_calls,
    )
    return report


async def _load_pending_days(*, limit: int) -> list[DayMessages]:
    """Group stored messages into UTC days, oldest first, capped by *limit*.

    The day list is aggregated in SQL. Message bodies are loaded only for
    those days, so a large backfill does not pull the whole table into memory.
    """
    from sqlalchemy import func, select

    from src.core.db.models import Message
    from src.core.db.session import get_session

    day_expr = func.date(func.timezone("UTC", Message.timestamp))
    pairs_stmt = (
        select(Message.from_contact_id, day_expr.label("day"))
        .where(Message.from_contact_id.isnot(None))
        .group_by(Message.from_contact_id, day_expr)
        .order_by(day_expr)
        .limit(limit)
    )
    async with get_session() as session:
        pair_rows = (await session.execute(pairs_stmt)).all()

    days: list[DayMessages] = []
    for contact_id, day in pair_rows:
        if contact_id is None or day is None:
            continue
        start = datetime.datetime.combine(day, datetime.time.min, tzinfo=datetime.UTC)
        end = start + datetime.timedelta(days=1)
        msg_stmt = (
            select(Message.text)
            .where(
                Message.from_contact_id == contact_id,
                Message.timestamp >= start,
                Message.timestamp < end,
            )
            .order_by(Message.timestamp, Message.external_message_id, Message.id)
        )
        async with get_session() as session:
            texts = [row[0] for row in (await session.execute(msg_stmt)).all()]
        days.append(DayMessages(contact_id=int(contact_id), day=day, texts=texts))
    return days
