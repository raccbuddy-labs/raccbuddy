"""Idempotent ingest of provider messages.

The rules live here so every store (in-memory tests, PostgreSQL) behaves
the same:

* Identity is ``(platform, external_chat_id, external_message_id)``.
* A second delivery of the same text is a duplicate.
* A second delivery with different text is an edit: the body and
  ``edited_at`` change, the original ``timestamp`` does not.
* Rows are ordered by that original timestamp, so an older message that
  arrives late sorts before newer ones.
* The per-chat cursor moves forward to the newest timestamp seen. It
  never moves backward.
"""

from __future__ import annotations

import datetime
from typing import Protocol

from src.core.sync.keys import ensure_utc
from src.core.sync.types import CursorState, IncomingRecord, IngestResult, StoredMessage


class MessageStore(Protocol):
    """Persistence port used by :class:`IngestService`."""

    async def get(self, key: tuple[str, str, str]) -> StoredMessage | None:
        """Return the row for this provider key, if any."""
        ...

    async def add(self, record: IncomingRecord, chat_id: int) -> StoredMessage:
        """Insert a new row. ``chat_id`` is the stable integer id."""
        ...

    async def apply_edit(
        self,
        stored: StoredMessage,
        *,
        text: str,
        edited_at: datetime.datetime,
    ) -> StoredMessage:
        """Update body and edit time. Leave the original timestamp."""
        ...

    async def find_legacy(self, record: IncomingRecord) -> StoredMessage | None:
        """A pre-sync row with the same text and send time, if one exists."""
        ...

    async def adopt_legacy(
        self,
        stored: StoredMessage,
        record: IncomingRecord,
        chat_id: int,
    ) -> StoredMessage:
        """Attach provider ids to a legacy row. Keep the original timestamp."""
        ...

    async def get_cursor(self, platform: str, scope: str) -> CursorState | None:
        """Return the watermark for this chat, if one has been stored."""
        ...

    async def put_cursor(self, cursor: CursorState) -> None:
        """Insert or replace the watermark."""
        ...


def coalesce(records: list[IncomingRecord]) -> tuple[list[IncomingRecord], int]:
    """Sort by send time and collapse repeated keys inside one batch.

    The surviving row keeps the earliest timestamp (the original send) and
    the body from the copy with the latest ``edited_at``. Identical repeats
    count as in-batch duplicates.
    """
    ordered = sorted(
        records,
        key=lambda rec: (
            ensure_utc(rec.timestamp),
            rec.edited_at or rec.timestamp,
            rec.external_message_id,
        ),
    )
    by_key: dict[tuple[str, str, str], IncomingRecord] = {}
    order: list[tuple[str, str, str]] = []
    duplicates = 0
    for rec in ordered:
        rec.timestamp = ensure_utc(rec.timestamp)
        if rec.edited_at is not None:
            rec.edited_at = ensure_utc(rec.edited_at)
        key = rec.key
        prev = by_key.get(key)
        if prev is None:
            by_key[key] = rec
            order.append(key)
            continue
        duplicates += 1
        by_key[key] = _merge(prev, rec)
    return [by_key[key] for key in order], duplicates


def _merge(prev: IncomingRecord, rec: IncomingRecord) -> IncomingRecord:
    """Combine two copies of the same provider message."""
    timestamp = min(prev.timestamp, rec.timestamp)
    prev_edit = prev.edited_at or datetime.datetime.min.replace(tzinfo=datetime.UTC)
    rec_edit = rec.edited_at or datetime.datetime.min.replace(tzinfo=datetime.UTC)
    if prev.edited_at or rec.edited_at:
        winner = rec if rec_edit >= prev_edit else prev
        text = winner.text
        edited_at = winner.edited_at or rec.edited_at or prev.edited_at
    elif rec.text != prev.text:
        winner = rec if rec.timestamp >= prev.timestamp else prev
        text = winner.text
        edited_at = None
    else:
        text = prev.text
        edited_at = None
    contact_id = rec.contact_id if rec.contact_id is not None else prev.contact_id
    return IncomingRecord(
        platform=prev.platform,
        external_chat_id=prev.external_chat_id,
        external_message_id=prev.external_message_id,
        text=text,
        timestamp=timestamp,
        sender_handle=rec.sender_handle or prev.sender_handle,
        sender_name=rec.sender_name or prev.sender_name,
        is_from_owner=prev.is_from_owner or rec.is_from_owner,
        is_bot_reply=prev.is_bot_reply or rec.is_bot_reply,
        edited_at=edited_at,
        contact_id=contact_id,
        is_group=prev.is_group or rec.is_group,
    )


def _page_reaches_watermark(
    group: list[IncomingRecord],
    current: CursorState | None,
) -> bool:
    """True when this page overlaps the stored newest message.

    No watermark means there is nothing to miss. A page of only-newer
    messages, or a page whose oldest timestamp is still after the
    watermark, has a hole in front of the cursor.
    """
    if current is None or current.newest_at is None:
        return True
    watermark = ensure_utc(current.newest_at)
    known = current.newest_external_id
    for rec in group:
        if known and rec.external_message_id == known:
            return True
        if ensure_utc(rec.timestamp) <= watermark:
            return True
    return False


def decide(existing: StoredMessage | None, incoming: IncomingRecord) -> str:
    """Return ``insert``, ``edit``, or ``duplicate``."""
    if existing is None:
        return "insert"
    if incoming.text != existing.text:
        return "edit"
    return "duplicate"


class IngestService:
    """Apply a page of messages to a :class:`MessageStore`."""

    def __init__(self, store: MessageStore) -> None:
        self.store = store

    async def ingest(
        self,
        records: list[IncomingRecord],
        *,
        chat_id_for: dict[tuple[str, str], int] | None = None,
        history_complete: bool = False,
        gap_blocks_complete: bool = False,
    ) -> IngestResult:
        """Insert, edit, or drop each record. Advance per-chat cursors.

        Blank texts are skipped (media-only events with no caption).
        ``chat_id_for`` maps ``(platform, external_chat_id)`` to the integer
        stored on the row. When omitted, the caller is expected to have set
        ids via :func:`src.core.sync.keys.stable_chat_id` on the store side;
        this service asks the store to add the record and the store assigns
        the id when ``chat_id_for`` is missing by importing the helper.
        """
        from src.core.sync.keys import stable_chat_id

        result = IngestResult()
        usable: list[IncomingRecord] = []
        for rec in records:
            if not rec.text or not rec.text.strip():
                result.skipped += 1
                continue
            if not rec.external_message_id or not rec.external_chat_id:
                result.skipped += 1
                continue
            usable.append(rec)

        coalesced, intra = coalesce(usable)
        result.duplicates += intra

        touched: dict[tuple[str, str], list[IncomingRecord]] = {}
        for rec in coalesced:
            existing = await self.store.get(rec.key)
            action = decide(existing, rec)
            if action == "insert":
                chat_id = None
                if chat_id_for is not None:
                    chat_id = chat_id_for.get((rec.platform, rec.external_chat_id))
                if chat_id is None:
                    chat_id = stable_chat_id(rec.platform, rec.external_chat_id)
                legacy = await self.store.find_legacy(rec)
                if legacy is not None:
                    # A row written before provider ids existed. Attach the
                    # id instead of inserting a second copy of the same text.
                    await self.store.adopt_legacy(legacy, rec, chat_id)
                    result.updated += 1
                else:
                    await self.store.add(rec, chat_id)
                    result.inserted += 1
            elif action == "edit" and existing is not None:
                edited_at = rec.edited_at or datetime.datetime.now(datetime.UTC)
                await self.store.apply_edit(
                    existing,
                    text=rec.text,
                    edited_at=edited_at,
                )
                result.updated += 1
            else:
                result.duplicates += 1
            touched.setdefault((rec.platform, rec.external_chat_id), []).append(rec)

        for (platform, scope), group in touched.items():
            await self._advance_cursor(
                platform,
                scope,
                group,
                history_complete=history_complete,
                gap_blocks_complete=gap_blocks_complete,
            )
        return result

    async def _advance_cursor(
        self,
        platform: str,
        scope: str,
        group: list[IncomingRecord],
        *,
        history_complete: bool,
        gap_blocks_complete: bool,
    ) -> None:
        """Move the watermark forward. Never backward.

        When *gap_blocks_complete* is set, a page that does not reach the
        stored watermark cannot mark the backfill finished. A short
        WhatsApp page used to do that and then never ask for the missing
        messages again.
        """
        current = await self.store.get_cursor(platform, scope)
        newest = max(group, key=lambda rec: (rec.timestamp, rec.external_message_id))
        oldest = min(group, key=lambda rec: (rec.timestamp, rec.external_message_id))
        newest_at = newest.timestamp
        oldest_at = oldest.timestamp
        newest_id = newest.external_message_id
        complete = history_complete
        if current is not None:
            if current.newest_at is not None and (
                current.newest_at,
                current.newest_external_id or "",
            ) > (newest_at, newest_id):
                newest_at = current.newest_at
                newest_id = current.newest_external_id
            if current.oldest_at is not None and current.oldest_at < oldest_at:
                oldest_at = current.oldest_at
            complete = current.backfill_complete or history_complete
        if (
            gap_blocks_complete
            and history_complete
            and not _page_reaches_watermark(group, current)
        ):
            complete = False
        await self.store.put_cursor(
            CursorState(
                platform=platform,
                scope=scope,
                newest_external_id=newest_id,
                newest_at=newest_at,
                oldest_at=oldest_at,
                backfill_complete=complete,
            )
        )
