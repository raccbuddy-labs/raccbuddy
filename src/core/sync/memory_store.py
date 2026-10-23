"""In-memory :class:`MessageStore` used by tests and as the reference store."""

from __future__ import annotations

import datetime

from src.core.sync.keys import ensure_utc
from src.core.sync.types import CursorState, IncomingRecord, StoredMessage


class InMemoryMessageStore:
    """Process-local store. Enforces the same key rules as PostgreSQL."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str], StoredMessage] = {}
        self.legacy: list[StoredMessage] = []
        self.cursors: dict[tuple[str, str], CursorState] = {}
        self._next_id = 1

    async def get(self, key: tuple[str, str, str]) -> StoredMessage | None:
        """Return the stored row for *key*."""
        return self.rows.get(key)

    async def add(self, record: IncomingRecord, chat_id: int) -> StoredMessage:
        """Insert. Raises if the key is already present."""
        if record.key in self.rows:
            raise ValueError(f"duplicate key {record.key}")
        stored = StoredMessage(
            id=self._next_id,
            platform=record.platform,
            external_chat_id=record.external_chat_id,
            external_message_id=record.external_message_id,
            chat_id=chat_id,
            text=record.text,
            timestamp=record.timestamp,
            edited_at=record.edited_at,
            is_from_owner=record.is_from_owner,
            is_bot_reply=record.is_bot_reply,
            contact_id=record.contact_id,
        )
        self._next_id += 1
        self.rows[record.key] = stored
        return stored

    async def find_legacy(self, record: IncomingRecord) -> StoredMessage | None:
        """Match a row that has no provider id yet.

        Prefer a row already labeled with this chat. Otherwise match a
        contact-scoped row whose chat id was never stored (a group shard).
        Same text and the same send second. The lowest id wins.
        """
        target = ensure_utc(record.timestamp).replace(microsecond=0)
        matches: list[tuple[int, int, StoredMessage]] = []
        for row in self.legacy:
            if row.platform != record.platform or row.text != record.text:
                continue
            if ensure_utc(row.timestamp).replace(microsecond=0) != target:
                continue
            chat_match = bool(row.external_chat_id) and (
                row.external_chat_id == record.external_chat_id
            )
            contact_match = (
                not row.external_chat_id
                and record.contact_id is not None
                and row.contact_id == record.contact_id
            )
            if chat_match or contact_match:
                matches.append((0 if chat_match else 1, row.id, row))
        if not matches:
            return None
        matches.sort()
        return matches[0][2]

    async def adopt_legacy(
        self,
        stored: StoredMessage,
        record: IncomingRecord,
        chat_id: int,
    ) -> StoredMessage:
        """Move a legacy row onto the provider key. Timestamp stays."""
        self.legacy = [row for row in self.legacy if row.id != stored.id]
        updated = StoredMessage(
            id=stored.id,
            platform=record.platform,
            external_chat_id=record.external_chat_id,
            external_message_id=record.external_message_id,
            chat_id=chat_id,
            text=stored.text,
            timestamp=stored.timestamp,
            edited_at=stored.edited_at,
            is_from_owner=record.is_from_owner,
            is_bot_reply=stored.is_bot_reply or record.is_bot_reply,
            contact_id=(
                stored.contact_id
                if stored.contact_id is not None
                else record.contact_id
            ),
        )
        self.rows[record.key] = updated
        return updated

    async def apply_edit(
        self,
        stored: StoredMessage,
        *,
        text: str,
        edited_at: datetime.datetime,
    ) -> StoredMessage:
        """Replace the body. The original timestamp stays put."""
        updated = StoredMessage(
            id=stored.id,
            platform=stored.platform,
            external_chat_id=stored.external_chat_id,
            external_message_id=stored.external_message_id,
            chat_id=stored.chat_id,
            text=text,
            timestamp=stored.timestamp,
            edited_at=edited_at,
            is_from_owner=stored.is_from_owner,
            is_bot_reply=stored.is_bot_reply,
            contact_id=stored.contact_id,
        )
        self.rows[
            updated.platform, updated.external_chat_id, updated.external_message_id
        ] = updated
        return updated

    async def get_cursor(self, platform: str, scope: str) -> CursorState | None:
        """Return the watermark, if any."""
        return self.cursors.get((platform, scope))

    async def put_cursor(self, cursor: CursorState) -> None:
        """Replace the watermark."""
        self.cursors[(cursor.platform, cursor.scope)] = cursor

    def list_chat(self, platform: str, external_chat_id: str) -> list[StoredMessage]:
        """Messages in send order, not insertion order."""
        rows = [
            row
            for row in self.rows.values()
            if row.platform == platform and row.external_chat_id == external_chat_id
        ]
        rows.extend(
            row
            for row in self.legacy
            if row.platform == platform and row.external_chat_id == external_chat_id
        )
        return sorted(
            rows, key=lambda row: (row.timestamp, row.external_message_id, row.id)
        )
