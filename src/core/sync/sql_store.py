"""PostgreSQL :class:`~src.core.sync.ingest.MessageStore`."""

from __future__ import annotations

import datetime
import logging

from sqlalchemy import and_, case, or_, select
from sqlalchemy.exc import IntegrityError

from src.core.sync.keys import ensure_utc

from src.core.db.crud import upsert_contact
from src.core.db.models import Message, SyncCursor
from src.core.db.session import get_session
from src.core.sync.types import CursorState, IncomingRecord, StoredMessage

logger = logging.getLogger(__name__)


def _to_stored(row: Message) -> StoredMessage:
    return StoredMessage(
        id=row.id,
        platform=row.platform,
        external_chat_id=row.external_chat_id or "",
        external_message_id=row.external_message_id or "",
        chat_id=row.chat_id,
        text=row.text,
        timestamp=row.timestamp,
        edited_at=row.edited_at,
        is_from_owner=row.is_from_owner,
        is_bot_reply=row.is_bot_reply,
        contact_id=row.from_contact_id,
    )


class SqlMessageStore:
    """Store that upserts contacts and writes ``messages`` / ``sync_cursors``."""

    def __init__(self, owner_id: int) -> None:
        self.owner_id = owner_id

    async def resolve_contact(self, record: IncomingRecord) -> IncomingRecord:
        """Attach ``contact_id`` for thread messages that do not have one yet.

        Owner DMs on Telegram stay unscoped (``contact_id`` remains None).
        WhatsApp outbound messages still carry the other person's handle, so
        they do get a contact row, with ``is_from_owner`` set on the message.
        """
        if record.contact_id is not None or record.is_bot_reply:
            return record
        if record.platform == "telegram" and record.is_from_owner:
            return record
        if not record.sender_handle:
            return record
        contact = await upsert_contact(
            owner_id=self.owner_id,
            contact_handle=record.sender_handle,
            platform=record.platform,
            contact_name=record.sender_name or record.sender_handle,
        )
        record.contact_id = contact.id
        return record

    async def get(self, key: tuple[str, str, str]) -> StoredMessage | None:
        """Look up a provider message id."""
        platform, external_chat_id, external_message_id = key
        async with get_session() as session:
            result = await session.execute(
                select(Message).where(
                    Message.platform == platform,
                    Message.external_chat_id == external_chat_id,
                    Message.external_message_id == external_message_id,
                )
            )
            row = result.scalar_one_or_none()
            return _to_stored(row) if row is not None else None

    async def add(self, record: IncomingRecord, chat_id: int) -> StoredMessage:
        """Insert one message. A concurrent duplicate becomes the existing row."""
        record = await self.resolve_contact(record)
        row = Message(
            platform=record.platform,
            chat_id=chat_id,
            from_contact_id=record.contact_id,
            text=record.text,
            is_bot_reply=record.is_bot_reply,
            is_from_owner=record.is_from_owner,
            external_chat_id=record.external_chat_id,
            external_message_id=record.external_message_id,
            timestamp=record.timestamp,
            edited_at=record.edited_at,
            ingested_at=datetime.datetime.now(datetime.UTC),
        )
        conflict = False
        async with get_session() as session:
            session.add(row)
            try:
                await session.commit()
                await session.refresh(row)
                return _to_stored(row)
            except IntegrityError:
                await session.rollback()
                conflict = True
        if not conflict:
            raise RuntimeError("insert failed without returning a row")
        existing = await self.get(record.key)
        if existing is None:
            raise RuntimeError(
                f"unique conflict for {record.platform}/{record.external_message_id} "
                "but the row was not found"
            )
        logger.info(
            "Provider message %s/%s already stored",
            record.platform,
            record.external_message_id,
        )
        return existing

    async def find_legacy(self, record: IncomingRecord) -> StoredMessage | None:
        """Find a pre-sync row with the same text and send second.

        Rows rewritten by the chat-id migration already have
        ``external_chat_id``. Group rows do not; those match on the contact.
        """
        record = await self.resolve_contact(record)
        second = ensure_utc(record.timestamp).replace(microsecond=0)
        upper = second + datetime.timedelta(seconds=1)
        chat_match = Message.external_chat_id == record.external_chat_id
        alternatives = [chat_match]
        if record.contact_id is not None:
            alternatives.append(
                and_(
                    Message.external_chat_id.is_(None),
                    Message.from_contact_id == record.contact_id,
                )
            )
        async with get_session() as session:
            result = await session.execute(
                select(Message)
                .where(
                    Message.platform == record.platform,
                    Message.external_message_id.is_(None),
                    Message.text == record.text,
                    Message.timestamp >= second,
                    Message.timestamp < upper,
                    or_(*alternatives),
                )
                .order_by(case((chat_match, 0), else_=1), Message.id)
                .limit(1)
            )
            row = result.scalar_one_or_none()
            return _to_stored(row) if row is not None else None

    async def adopt_legacy(
        self,
        stored: StoredMessage,
        record: IncomingRecord,
        chat_id: int,
    ) -> StoredMessage:
        """Fill provider ids on a legacy row. The send time is not touched."""
        async with get_session() as session:
            result = await session.execute(
                select(Message).where(Message.id == stored.id)
            )
            row = result.scalar_one()
            row.external_chat_id = record.external_chat_id
            row.external_message_id = record.external_message_id
            row.chat_id = chat_id
            row.is_from_owner = record.is_from_owner
            if record.is_bot_reply:
                row.is_bot_reply = True
            try:
                await session.commit()
                await session.refresh(row)
                return _to_stored(row)
            except IntegrityError:
                await session.rollback()
        existing = await self.get(record.key)
        if existing is None:
            raise RuntimeError(
                f"legacy adopt conflict for {record.platform}/{record.external_message_id}"
            )
        return existing

    async def apply_edit(
        self,
        stored: StoredMessage,
        *,
        text: str,
        edited_at: datetime.datetime,
    ) -> StoredMessage:
        """Update text and edited_at. Original timestamp is kept."""
        async with get_session() as session:
            result = await session.execute(
                select(Message).where(Message.id == stored.id)
            )
            row = result.scalar_one()
            row.text = text
            row.edited_at = edited_at
            await session.commit()
            await session.refresh(row)
            return _to_stored(row)

    async def get_cursor(self, platform: str, scope: str) -> CursorState | None:
        """Return the sync watermark for this chat."""
        async with get_session() as session:
            result = await session.execute(
                select(SyncCursor).where(
                    SyncCursor.platform == platform,
                    SyncCursor.scope == scope,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return CursorState(
                platform=row.platform,
                scope=row.scope,
                newest_external_id=row.newest_external_id,
                newest_at=row.newest_at,
                oldest_at=row.oldest_at,
                backfill_complete=row.backfill_complete,
            )

    async def put_cursor(self, cursor: CursorState) -> None:
        """Upsert the watermark."""
        async with get_session() as session:
            result = await session.execute(
                select(SyncCursor).where(
                    SyncCursor.platform == cursor.platform,
                    SyncCursor.scope == cursor.scope,
                )
            )
            row = result.scalar_one_or_none()
            now = datetime.datetime.now(datetime.UTC)
            if row is None:
                row = SyncCursor(
                    platform=cursor.platform,
                    scope=cursor.scope,
                    newest_external_id=cursor.newest_external_id,
                    newest_at=cursor.newest_at,
                    oldest_at=cursor.oldest_at,
                    backfill_complete=cursor.backfill_complete,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.newest_external_id = cursor.newest_external_id
                row.newest_at = cursor.newest_at
                row.oldest_at = cursor.oldest_at
                row.backfill_complete = cursor.backfill_complete
                row.updated_at = now
            await session.commit()
