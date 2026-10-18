"""Parse a Telegram Desktop ``result.json`` export into ingest records.

The Bot API cannot return chat history. Desktop's "Export Telegram data"
produces a JSON file that does. Text entities may be a string or a list of
strings and ``{"type", "text"}`` objects; both are flattened.
"""

from __future__ import annotations

import datetime
from typing import Any

from src.core.sync.keys import ensure_utc
from src.core.sync.types import IncomingRecord


def parse_telegram_export(payload: dict[str, Any]) -> list[IncomingRecord]:
    """Convert one exported chat object into ingest records.

    Service messages (pins, joins) are skipped. ``from_id`` equal to the
    chat's own saved owner is not known here; callers can pass
    ``owner_from_id`` via :func:`parse_telegram_export_owned`.
    """
    return parse_telegram_export_owned(payload, owner_from_id=None)


def parse_telegram_export_owned(
    payload: dict[str, Any],
    *,
    owner_from_id: str | None,
) -> list[IncomingRecord]:
    """Like :func:`parse_telegram_export`, marking the owner's own messages."""
    chat_id = str(payload.get("id") or payload.get("name") or "telegram-export")
    messages = payload.get("messages") or []
    records: list[IncomingRecord] = []
    if not isinstance(messages, list):
        return records
    for raw in messages:
        if not isinstance(raw, dict):
            continue
        if raw.get("type") not in (None, "message"):
            continue
        text = _flatten_text(raw.get("text"))
        if not text.strip():
            continue
        message_id = raw.get("id")
        if message_id is None:
            continue
        timestamp = _parse_date(raw)
        if timestamp is None:
            continue
        from_id = str(raw.get("from_id") or raw.get("from") or "")
        edited = _parse_optional_date(raw.get("edited") or raw.get("edited_unixtime"))
        records.append(
            IncomingRecord(
                platform="telegram",
                external_chat_id=chat_id,
                external_message_id=str(message_id),
                text=text,
                timestamp=timestamp,
                sender_handle=from_id,
                sender_name=_as_str(raw.get("from")),
                is_from_owner=bool(owner_from_id) and from_id == owner_from_id,
                edited_at=edited,
            )
        )
    return records


def _flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
        return "".join(parts)
    return str(value)


def _parse_date(raw: dict[str, Any]) -> datetime.datetime | None:
    unix = raw.get("date_unixtime")
    if unix is not None and str(unix).isdigit():
        return datetime.datetime.fromtimestamp(int(unix), tz=datetime.UTC)
    return _parse_optional_date(raw.get("date"))


def _parse_optional_date(value: Any) -> datetime.datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        return datetime.datetime.fromtimestamp(int(value), tz=datetime.UTC)
    if isinstance(value, str):
        try:
            return ensure_utc(datetime.datetime.fromisoformat(value))
        except ValueError:
            return None
    return None


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None
