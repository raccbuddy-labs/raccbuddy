"""REST API for receiving messages from external platform bridges.

Exposes POST /api/messages so that Node.js / Go / etc. services
can push messages into the same pipeline as Telegram.

Authentication
--------------
When ``API_SECRET_KEY`` is set in the environment, every request to
``POST /api/messages`` must include the header::

    X-API-Key: <API_SECRET_KEY>

Requests without the correct key receive 401 Unauthorized.
Leave ``API_SECRET_KEY`` empty to disable auth (development only).
"""

from __future__ import annotations

import datetime
import logging

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from src.core.config import settings
from src.core.sync.facade import persist_incoming, read_cursor
from src.core.sync.keys import ensure_utc, fallback_external_message_id
from src.core.sync.telegram_export import parse_telegram_export_owned
from src.core.sync.types import IncomingRecord

logger = logging.getLogger(__name__)

_MAX_BATCH = 500

api = FastAPI(title="RaccBuddy Core API", version="0.1.0")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class IncomingMessage(BaseModel):
    """Schema for messages arriving from external bridges (WhatsApp, etc.)."""

    platform: str
    chat_id: str
    from_id: str
    contact_name: str | None = None
    text: str
    timestamp: str | None = None
    is_group: bool | None = False
    group_name: str | None = None
    from_me: bool | None = False  # True if message is from the owner
    # Provider message id. When omitted, a hash of the payload is used so
    # retries of the same body do not insert a second row.
    external_message_id: str | None = None
    edited_at: str | None = None


class IncomingBatch(BaseModel):
    """A page of history or a short incremental sync from one connector."""

    messages: list[IncomingMessage]
    # True when the connector fetched fewer messages than it asked for, so
    # older history is exhausted for this scope.
    history_complete: bool = False


class TelegramExportBody(BaseModel):
    """Telegram Desktop ``result.json`` contents, plus an optional owner id."""

    export: dict
    owner_from_id: str | None = None


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------


def _verify_api_key(x_api_key: str | None) -> None:
    """Raise HTTP 401 if the API key is configured but not provided / wrong.

    Args:
        x_api_key: Value of the ``X-API-Key`` request header.

    Raises:
        HTTPException: 401 when auth fails.
    """
    configured_key = settings.api_secret_key
    if not configured_key:
        # Auth is disabled — accept all requests (dev / LAN-only setups).
        return
    if x_api_key != configured_key:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing X-API-Key header.",
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@api.get("/health")
async def health() -> dict[str, str]:
    """Simple liveness health-check endpoint."""
    return {"status": "ok"}


def _owner_or_503() -> int:
    """Return the configured owner id or raise 503."""
    owner_id = settings.owner_telegram_id
    if not owner_id:
        raise HTTPException(
            status_code=503,
            detail=("OWNER_TELEGRAM_ID not configured — run /start on Telegram first."),
        )
    return owner_id


def _parse_ts(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    try:
        return ensure_utc(datetime.datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def record_from_payload(msg: IncomingMessage) -> IncomingRecord:
    """Map a bridge payload onto an :class:`IncomingRecord`.

    Chat ids are *not* derived with Python's ``hash()``. That value is
    salted per process, so the same WhatsApp chat was stored under a new
    integer after every restart. :func:`persist_incoming` hashes with SHA-256
    instead, and Telegram keeps the integer id Telegram assigned.
    """
    timestamp = _parse_ts(msg.timestamp) or datetime.datetime.now(datetime.UTC)
    is_owner = bool(msg.from_me)
    if (
        msg.platform == "whatsapp"
        and settings.owner_whatsapp_number
        and msg.from_id == settings.owner_whatsapp_number
    ):
        is_owner = True
    external_id = msg.external_message_id or fallback_external_message_id(
        platform=msg.platform,
        external_chat_id=msg.chat_id,
        timestamp=timestamp,
        sender_handle=msg.from_id,
        text=msg.text,
    )
    return IncomingRecord(
        platform=msg.platform,
        external_chat_id=msg.chat_id,
        external_message_id=external_id,
        text=msg.text,
        timestamp=timestamp,
        sender_handle=msg.from_id,
        sender_name=msg.contact_name,
        is_from_owner=is_owner,
        edited_at=_parse_ts(msg.edited_at),
        is_group=bool(msg.is_group),
    )


@api.post("/api/messages")
async def receive_message(
    msg: IncomingMessage,
    x_api_key: str | None = Header(default=None),
) -> dict[str, int | str]:
    """Accept a message from an external bridge and persist it idempotently.

    A repeated POST of the same provider message id is a duplicate, not a
    second row. A repeated POST with the same id and a different body is
    stored as an edit and keeps the original send time.

    Args:
        msg: Incoming message payload from the bridge.
        x_api_key: Value of the ``X-API-Key`` header (injected by FastAPI).

    Returns:
        Status plus insert / update / duplicate counts.

    Raises:
        HTTPException: 401 on auth failure, 503 if owner not configured,
            500 on internal error.
    """
    _verify_api_key(x_api_key)
    owner_id = _owner_or_503()
    try:
        result = await persist_incoming([record_from_payload(msg)], owner_id=owner_id)
        logger.info(
            "Ingested %s message from %s (%s)",
            msg.platform,
            msg.contact_name or msg.from_id,
            result.as_dict(),
        )
        return {"status": "ok", **result.as_dict()}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to save incoming message from %s", msg.from_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@api.post("/api/messages/batch")
async def receive_batch(
    batch: IncomingBatch,
    x_api_key: str | None = Header(default=None),
) -> dict[str, int | str | bool]:
    """Ingest a history page. Re-running the same page inserts nothing.

    Summaries and embeddings are not computed inside this request. The
    periodic summary job drains them in bounded batches.
    """
    _verify_api_key(x_api_key)
    owner_id = _owner_or_503()
    if len(batch.messages) > _MAX_BATCH:
        raise HTTPException(
            status_code=413,
            detail=f"Batch limit is {_MAX_BATCH} messages.",
        )
    try:
        records = [record_from_payload(msg) for msg in batch.messages]
        result = await persist_incoming(
            records,
            owner_id=owner_id,
            history_complete=batch.history_complete,
        )
        return {
            "status": "ok",
            "catchup": "deferred",
            **result.as_dict(),
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to ingest message batch")
        raise HTTPException(status_code=500, detail="Internal server error")


@api.get("/api/sync/cursor")
async def sync_cursor(
    platform: str,
    scope: str,
    x_api_key: str | None = Header(default=None),
) -> dict[str, str | bool | None]:
    """Return the resume watermark for one provider chat."""
    _verify_api_key(x_api_key)
    owner_id = _owner_or_503()
    cursor = await read_cursor(platform, scope, owner_id=owner_id)
    if cursor is None:
        return {
            "platform": platform,
            "scope": scope,
            "newest_external_id": None,
            "newest_at": None,
            "oldest_at": None,
            "backfill_complete": False,
        }
    return {
        "platform": platform,
        "scope": scope,
        "newest_external_id": cursor.newest_external_id,
        "newest_at": cursor.newest_at.isoformat() if cursor.newest_at else None,
        "oldest_at": cursor.oldest_at.isoformat() if cursor.oldest_at else None,
        "backfill_complete": cursor.backfill_complete,
    }


@api.post("/api/import/telegram")
async def import_telegram(
    body: TelegramExportBody,
    x_api_key: str | None = Header(default=None),
) -> dict[str, int | str | bool]:
    """Backfill one Telegram Desktop export.

    The Bot API has no method that returns prior messages. This endpoint
    is the supported history path for Telegram. Re-posting the same file
    does not duplicate rows.
    """
    _verify_api_key(x_api_key)
    owner_id = _owner_or_503()
    records = parse_telegram_export_owned(body.export, owner_from_id=body.owner_from_id)
    try:
        result = await persist_incoming(
            records,
            owner_id=owner_id,
            history_complete=True,
        )
        return {"status": "ok", "catchup": "deferred", **result.as_dict()}
    except Exception:
        logger.exception("Failed to import Telegram export")
        raise HTTPException(status_code=500, detail="Internal server error")
