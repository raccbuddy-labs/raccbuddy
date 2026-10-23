"""Value types for history ingest. No I/O."""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field


@dataclass
class IncomingRecord:
    """One provider message, already normalized.

    ``timestamp`` is when the message was sent, not when RaccBuddy saw it.
    ``external_message_id`` is the provider's stable id for that message.
    """

    platform: str
    external_chat_id: str
    external_message_id: str
    text: str
    timestamp: datetime.datetime
    sender_handle: str = ""
    sender_name: str | None = None
    is_from_owner: bool = False
    is_bot_reply: bool = False
    edited_at: datetime.datetime | None = None
    contact_id: int | None = None
    is_group: bool = False

    @property
    def key(self) -> tuple[str, str, str]:
        """Idempotency key. Safe to re-apply."""
        return (self.platform, self.external_chat_id, self.external_message_id)


@dataclass
class StoredMessage:
    """A message row as the ingest layer sees it."""

    id: int
    platform: str
    external_chat_id: str
    external_message_id: str
    chat_id: int
    text: str
    timestamp: datetime.datetime
    edited_at: datetime.datetime | None = None
    is_from_owner: bool = False
    is_bot_reply: bool = False
    contact_id: int | None = None


@dataclass
class CursorState:
    """Watermark for one provider scope (usually one chat)."""

    platform: str
    scope: str
    newest_external_id: str | None = None
    newest_at: datetime.datetime | None = None
    oldest_at: datetime.datetime | None = None
    backfill_complete: bool = False


@dataclass
class IngestResult:
    """Counts from one ingest call."""

    inserted: int = 0
    updated: int = 0
    duplicates: int = 0
    skipped: int = 0

    def as_dict(self) -> dict[str, int]:
        """JSON-ready counts."""
        return {
            "inserted": self.inserted,
            "updated": self.updated,
            "duplicates": self.duplicates,
            "skipped": self.skipped,
        }

    @property
    def is_replay(self) -> bool:
        """True when nothing new was written.

        Non-integers (test doubles that do not set counts) are not replays,
        so a handler mock still falls through to reply generation.
        """
        if not isinstance(self.inserted, int) or not isinstance(self.updated, int):
            return False
        return self.inserted == 0 and self.updated == 0


@dataclass
class SyncPlan:
    """Which fetched messages still need to be sent to the core."""

    to_send: list[IncomingRecord] = field(default_factory=list)
    gap: bool = False
