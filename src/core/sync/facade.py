"""Process-wide entry point used by the API and the Telegram handlers."""

from __future__ import annotations

from src.core.sync.ingest import IngestService
from src.core.sync.sql_store import SqlMessageStore
from src.core.sync.types import IncomingRecord, IngestResult


async def persist_incoming(
    records: list[IncomingRecord],
    *,
    owner_id: int,
    history_complete: bool = False,
    gap_blocks_complete: bool = False,
) -> IngestResult:
    """Persist provider messages for *owner_id*. Safe to call again.

    Does not run sentiment, relationship scoring, or embeddings. Live
    handlers that want enrichment do that only for rows this call inserted.
    ``gap_blocks_complete`` stops a page that misses the cursor from
    marking the backfill finished.
    """
    store = SqlMessageStore(owner_id)
    service = IngestService(store)
    return await service.ingest(
        records,
        history_complete=history_complete,
        gap_blocks_complete=gap_blocks_complete,
    )


async def read_cursor(platform: str, scope: str, *, owner_id: int):
    """Return the stored watermark, or None when this chat has never synced.

    ``owner_id`` is accepted so callers share one signature with
    :func:`persist_incoming`. Cursors are not owner-scoped today because
    the deployment is single-owner; the argument is kept so a future
    multi-owner split does not change the call sites.
    """
    del owner_id
    store = SqlMessageStore(owner_id=0)
    return await store.get_cursor(platform, scope)
