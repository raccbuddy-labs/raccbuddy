"""Shared batching helpers for pull connectors."""

from __future__ import annotations

from collections.abc import Iterable

from src.core.sync.ingest import IngestService
from src.core.sync.types import IncomingRecord, IngestResult

# Same ceiling as POST /api/messages/batch. A backfill page must not
# become one unbounded transaction.
DEFAULT_CHUNK = 500


def add_results(total: IngestResult, part: IngestResult) -> None:
    """Add *part* into *total*."""
    total.inserted += part.inserted
    total.updated += part.updated
    total.duplicates += part.duplicates
    total.skipped += part.skipped


def chunks(items: list[IncomingRecord], size: int) -> Iterable[list[IncomingRecord]]:
    """Yield successive slices of *size*."""
    step = size if size > 0 else DEFAULT_CHUNK
    for start in range(0, len(items), step):
        yield items[start : start + step]


async def ingest_chunks(
    service: IngestService,
    records: list[IncomingRecord],
    *,
    history_complete: bool,
    chunk_size: int = DEFAULT_CHUNK,
) -> IngestResult:
    """Ingest *records* in bounded batches.

    ``history_complete`` is applied only on the last batch. Marking an
    earlier batch complete would advance the cursor past messages that
    have not been written yet.
    """
    result = IngestResult()
    if not records:
        return result
    batches = list(chunks(records, chunk_size))
    last = len(batches) - 1
    for index, batch in enumerate(batches):
        part = await service.ingest(
            batch,
            history_complete=history_complete and index == last,
        )
        add_results(result, part)
    return result
