"""Today's summary uses the windowed catch-up path, not a 20-line clip."""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.summarizer import summarize_daily


@pytest.mark.asyncio
async def test_daily_summary_windows_a_large_backfill_instead_of_clipping() -> None:
    day = datetime.datetime.now(datetime.UTC).date()
    messages = [
        SimpleNamespace(text=f"message {index} " + ("word " * 30))
        for index in range(100)
    ]
    generates: list[str] = []

    async def generate(prompt: str, system: str = "") -> str:
        generates.append(prompt)
        return "chunk"

    async def embed(text: str) -> list[float]:
        return [0.25]

    with (
        patch(
            "src.summarizer.get_summary_for_date",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "src.summarizer.get_messages_since",
            new_callable=AsyncMock,
            return_value=messages,
        ) as fetch,
        patch("src.summarizer.generate", new=generate),
        patch("src.summarizer.embed", new=embed),
        patch("src.summarizer.save_summary", new_callable=AsyncMock) as save,
    ):
        text = await summarize_daily(9)

    assert text is not None
    assert text.count("chunk") == 3
    assert len(generates) == 3
    assert fetch.await_args.kwargs["limit"] is None
    saved = save.await_args.kwargs
    assert saved["date"] == day
    assert saved["embedding"] == [0.25]
    # The old path clipped every line to 100 characters before the model.
    assert all(
        len(line) > 100 for line in generates[0].splitlines() if line.startswith("- ")
    )
