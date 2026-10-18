"""Bounded summarization for a large history backfill."""

from __future__ import annotations

import datetime

import pytest

from src.core.sync.catchup import DayMessages, summarize_days


def _day(count: int, day: datetime.date | None = None) -> DayMessages:
    return DayMessages(
        contact_id=1,
        day=day or datetime.date(2024, 1, 1),
        texts=[f"message {index}" for index in range(count)],
    )


@pytest.mark.asyncio
async def test_large_day_is_windowed_not_one_call_per_message() -> None:
    generates: list[str] = []
    embeds: list[str] = []

    async def generate(prompt: str) -> str:
        generates.append(prompt)
        return "summary chunk"

    async def embed(text: str) -> list[float]:
        embeds.append(text)
        return [0.1, 0.2]

    report = await summarize_days(
        [_day(100)],
        generate=generate,
        embed=embed,
        window=40,
        max_days=10,
        max_llm_calls=30,
        pause_seconds=0,
    )

    # 100 messages / 40 = 3 generations and a single embedding, not 100.
    assert report.llm_calls == 3
    assert report.embed_calls == 1
    assert report.days_summarized == 1
    assert report.days_deferred == 0
    assert len(generates) == 3
    assert len(embeds) == 1
    assert embeds[0].count("summary chunk") == 3


@pytest.mark.asyncio
async def test_day_that_exceeds_the_call_budget_is_deferred_whole() -> None:
    async def generate(prompt: str) -> str:
        raise AssertionError("should not summarize a day that does not fit")

    async def embed(text: str) -> list[float]:
        raise AssertionError("should not embed a deferred day")

    report = await summarize_days(
        [_day(100)],
        generate=generate,
        embed=embed,
        window=40,
        max_days=10,
        max_llm_calls=2,
        pause_seconds=0,
    )
    assert report.llm_calls == 0
    assert report.embed_calls == 0
    assert report.days_summarized == 0
    assert report.days_deferred == 1


@pytest.mark.asyncio
async def test_max_days_caps_a_multi_year_backfill() -> None:
    calls = 0

    async def generate(prompt: str) -> str:
        nonlocal calls
        calls += 1
        return "ok"

    async def embed(text: str) -> list[float]:
        return [1.0]

    days = [_day(1, datetime.date(2020, 1, index)) for index in range(1, 15)]
    report = await summarize_days(
        days,
        generate=generate,
        embed=embed,
        window=40,
        max_days=3,
        max_llm_calls=30,
        pause_seconds=0,
    )
    assert report.days_summarized == 3
    assert report.days_deferred == 11
    assert calls == 3
