"""Stable ids for provider chats and messages.

Python's builtin ``hash()`` is salted per process, so it must not be used
as a chat id. These helpers are pure functions of their inputs.
"""

from __future__ import annotations

import datetime
import hashlib

_INT64_MIN = -(2**63)
_INT64_MAX = 2**63


def stable_chat_id(platform: str, external_chat_id: str) -> int:
    """Return a signed int64 chat id that does not change across processes.

    Telegram chat ids are already stable integers (including negative
    supergroup ids) and are preserved so rows written before this change
    still group with new ones. Every other platform is SHA-256 truncated
    to 63 bits.
    """
    if platform == "telegram":
        try:
            value = int(external_chat_id)
        except (TypeError, ValueError):
            value = None
        if value is not None and _INT64_MIN <= value < _INT64_MAX:
            return value

    digest = hashlib.sha256(f"{platform}\n{external_chat_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF


def fallback_external_message_id(
    *,
    platform: str,
    external_chat_id: str,
    timestamp: datetime.datetime,
    sender_handle: str,
    text: str,
) -> str:
    """Id for a payload that arrived without a provider message id.

    Retries of the same payload collide. A real edit without an id looks
    like a new message; connectors that can see edits must send the
    provider id instead.
    """
    raw = "\n".join(
        [
            platform,
            external_chat_id,
            timestamp.isoformat(),
            sender_handle,
            text,
        ]
    )
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def ensure_utc(ts: datetime.datetime) -> datetime.datetime:
    """Coerce *ts* to timezone-aware UTC. Naive values are treated as UTC."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=datetime.UTC)
    return ts.astimezone(datetime.UTC)
