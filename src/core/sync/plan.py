"""Decide which fetched messages an incremental sync should send.

The provider may return a newest-first page with no server-side cursor
(WhatsApp Web's ``fetchMessages`` works this way). Overlap plus the
idempotency key makes a re-send safe. ``gap`` is true when the page does
not reach the stored watermark, which means the fetch limit has to grow
before older messages can be trusted as synced.
"""

from __future__ import annotations

import datetime

from src.core.sync.keys import ensure_utc
from src.core.sync.types import CursorState, IncomingRecord, SyncPlan


def plan_incremental(
    fetched: list[IncomingRecord],
    cursor: CursorState | None,
    *,
    overlap: datetime.timedelta = datetime.timedelta(seconds=120),
    max_messages: int | None = None,
) -> SyncPlan:
    """Select messages to forward and whether the page missed the cursor.

    With no cursor, the whole page is a backfill (optionally capped to the
    most recent ``max_messages``). With a cursor, messages older than
    ``newest_at - overlap`` are dropped unless the page never reaches that
    cutoff, in which case ``gap`` is set and the caller should fetch a
    larger page.
    """
    ordered = sorted(
        fetched,
        key=lambda rec: (ensure_utc(rec.timestamp), rec.external_message_id),
    )
    if max_messages is not None and max_messages >= 0:
        ordered = ordered[-max_messages:]

    if cursor is None or cursor.newest_at is None:
        return SyncPlan(to_send=ordered, gap=False)

    cutoff = ensure_utc(cursor.newest_at) - overlap
    known_id = cursor.newest_external_id
    reached = False
    selected: list[IncomingRecord] = []
    for rec in ordered:
        ts = ensure_utc(rec.timestamp)
        if known_id and rec.external_message_id == known_id:
            reached = True
        if ts >= cutoff:
            selected.append(rec)
            if ts <= ensure_utc(cursor.newest_at):
                reached = True

    gap = bool(ordered) and not reached and ensure_utc(ordered[0].timestamp) > cutoff
    return SyncPlan(to_send=selected, gap=gap)
