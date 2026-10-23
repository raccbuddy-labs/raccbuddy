"""Matrix Client-Server history sync.

Uses ``GET /sync`` for the incremental cursor (``next_batch``) and
``GET /rooms/{id}/messages?dir=b`` to walk older history. Both tokens are
stored on cursor scopes that are not room ids, so the per-room message
watermark can keep the latest event id.

Only ``m.room.message`` events with a text body are stored (``m.text``,
``m.notice``, ``m.emote``). An ``m.replace`` relation is an edit of the
original event id: the body changes, the original ``origin_server_ts``
stays. Encrypted rooms (``m.room.encryption`` in state, or
``m.room.encrypted`` events) are skipped. This connector does not implement
Olm or Megolm.

Limits:

* The homeserver only returns history it still has. Retention and
  purged state are not recoverable from this API.
* Invites and left rooms are ignored.
* ``is_group`` is true when the sync payload reports more than two joined
  members. A later incremental sync that omits that summary does not
  rewrite the flag on rows already stored.
* One run pulls at most ``max_per_room`` historical events. The pagination
  token is saved and the next run continues. ``backfill_complete`` stays
  false while the server still returns an ``end`` token.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from src.connectors.common import add_results, ingest_chunks
from src.core.sync.ingest import IngestService
from src.core.sync.types import CursorState, IncomingRecord, IngestResult

logger = logging.getLogger(__name__)

PLATFORM = "matrix"
SYNC_SCOPE = "__sync__"
_TEXT_MSGTYPES = frozenset({"m.text", "m.notice", "m.emote"})


class MatrixTransport(Protocol):
    """Sync and room-message calls. Tests supply a fake."""

    def sync(self, since: str | None) -> dict[str, Any]:
        """``GET /sync``. *since* is the previous ``next_batch``."""
        ...

    def messages(self, room_id: str, from_token: str, limit: int) -> dict[str, Any]:
        """``GET /messages?dir=b``."""
        ...


def backfill_scope(room_id: str) -> str:
    """Cursor scope for the backward pagination token of *room_id*."""
    return f"backfill:{room_id}"


def _event_time(event: dict[str, Any]) -> datetime.datetime | None:
    raw = event.get("origin_server_ts")
    if not isinstance(raw, int | float):
        return None
    return datetime.datetime.fromtimestamp(raw / 1000, tz=datetime.UTC)


def room_is_encrypted(room: dict[str, Any]) -> bool:
    """True when this room snapshot advertises encryption."""
    for bucket in ("state", "timeline"):
        events = (room.get(bucket) or {}).get("events") or []
        for event in events:
            if event.get("type") in ("m.room.encryption", "m.room.encrypted"):
                return True
    return False


def room_is_group(room: dict[str, Any]) -> bool:
    """True when the snapshot shows more than two joined members."""
    summary = room.get("summary") or {}
    count = summary.get("m.joined_member_count")
    if isinstance(count, int):
        return count > 2
    members: set[str] = set()
    for event in (room.get("state") or {}).get("events") or []:
        if event.get("type") != "m.room.member":
            continue
        if (event.get("content") or {}).get("membership") != "join":
            continue
        member = event.get("state_key") or event.get("sender")
        if isinstance(member, str) and member:
            members.add(member)
    return len(members) > 2


def parse_event(
    room_id: str,
    event: dict[str, Any],
    *,
    owner_mxid: str,
    is_group: bool,
) -> IncomingRecord | None:
    """Map one timeline event onto the ingest contract, or None to skip."""
    if event.get("type") != "m.room.message":
        return None
    content = event.get("content") or {}
    relates = content.get("m.relates_to") or {}
    sent_at = _event_time(event)
    if sent_at is None:
        return None
    sender = event.get("sender") or ""
    from_owner = bool(owner_mxid) and sender == owner_mxid
    if relates.get("rel_type") == "m.replace":
        target = relates.get("event_id")
        body = ((content.get("m.new_content") or {}).get("body") or "").strip()
        if not isinstance(target, str) or not target or not body:
            return None
        return IncomingRecord(
            platform=PLATFORM,
            external_chat_id=room_id,
            external_message_id=target,
            text=body,
            timestamp=sent_at,
            edited_at=sent_at,
            sender_handle=sender,
            sender_name=sender,
            is_from_owner=from_owner,
            is_group=is_group,
        )
    if content.get("msgtype") not in _TEXT_MSGTYPES:
        return None
    body = (content.get("body") or "").strip()
    event_id = event.get("event_id")
    if not body or not isinstance(event_id, str) or not event_id:
        return None
    return IncomingRecord(
        platform=PLATFORM,
        external_chat_id=room_id,
        external_message_id=event_id,
        text=body,
        timestamp=sent_at,
        sender_handle=sender,
        sender_name=sender,
        is_from_owner=from_owner,
        is_group=is_group,
    )


def _parse_events(
    room_id: str,
    events: list[dict[str, Any]],
    *,
    owner_mxid: str,
    is_group: bool,
) -> tuple[list[IncomingRecord], int]:
    records: list[IncomingRecord] = []
    skipped = 0
    for event in events:
        if event.get("type") != "m.room.message":
            continue
        record = parse_event(
            room_id,
            event,
            owner_mxid=owner_mxid,
            is_group=is_group,
        )
        if record is None:
            skipped += 1
        else:
            records.append(record)
    return records, skipped


async def _backfill_room(
    transport: MatrixTransport,
    *,
    room_id: str,
    room: dict[str, Any],
    cursor: CursorState | None,
    max_per_room: int,
    page_size: int,
    pause_seconds: float,
) -> tuple[list[dict[str, Any]], str | None, bool]:
    """Walk ``/messages`` backward. Returns events, next token, complete."""
    if cursor is not None and cursor.backfill_complete:
        return [], cursor.newest_external_id, True
    if cursor is not None and cursor.newest_external_id:
        token: str | None = cursor.newest_external_id
    else:
        token = (room.get("timeline") or {}).get("prev_batch")
    if not token:
        return [], None, True

    cap = max_per_room if max_per_room > 0 else 500
    step = page_size if page_size > 0 else 50
    collected: list[dict[str, Any]] = []
    complete = False
    while token and len(collected) < cap:
        limit = min(step, cap - len(collected))
        page = await asyncio.to_thread(transport.messages, room_id, token, limit)
        chunk = page.get("chunk") or []
        collected.extend(chunk)
        token = page.get("end") or None
        if not chunk or not token:
            complete = True
            break
        if pause_seconds > 0 and len(collected) < cap:
            await asyncio.sleep(pause_seconds)
    if token and len(collected) >= cap:
        complete = False
    return collected, token, complete


async def sync_matrix(
    transport: MatrixTransport,
    store,
    *,
    owner_mxid: str,
    max_per_room: int = 500,
    page_size: int = 50,
    pause_seconds: float = 0.0,
    chunk_size: int = 500,
) -> IngestResult:
    """One ``/sync`` plus per-room history pages that are not finished yet.

    Re-running with the stored ``next_batch`` and a finished backfill does
    not insert the same events again. Encrypted rooms are left untouched.
    """
    result = IngestResult()
    service = IngestService(store)
    sync_cursor = await store.get_cursor(PLATFORM, SYNC_SCOPE)
    since = sync_cursor.newest_external_id if sync_cursor else None
    payload = await asyncio.to_thread(transport.sync, since)
    rooms = ((payload.get("rooms") or {}).get("join")) or {}

    for room_id, room in rooms.items():
        if not isinstance(room, dict):
            continue
        if room_is_encrypted(room):
            logger.info("Skipping encrypted Matrix room %s", room_id)
            continue
        grouped = room_is_group(room)
        timeline = (room.get("timeline") or {}).get("events") or []
        records, skipped = _parse_events(
            room_id,
            timeline,
            owner_mxid=owner_mxid,
            is_group=grouped,
        )
        result.skipped += skipped

        bf_cursor = await store.get_cursor(PLATFORM, backfill_scope(room_id))
        history_failed = False
        try:
            history, token, complete = await _backfill_room(
                transport,
                room_id=room_id,
                room=room,
                cursor=bf_cursor,
                max_per_room=max_per_room,
                page_size=page_size,
                pause_seconds=pause_seconds,
            )
        except Exception:
            logger.exception("Matrix history failed for %s", room_id)
            history_failed = True
            history, token, complete = [], None, False
        extra, extra_skipped = _parse_events(
            room_id,
            history,
            owner_mxid=owner_mxid,
            is_group=grouped,
        )
        result.skipped += extra_skipped
        records.extend(extra)
        part = await ingest_chunks(
            service,
            records,
            history_complete=complete and not history_failed,
            chunk_size=chunk_size,
        )
        add_results(result, part)
        if not history_failed:
            await store.put_cursor(
                CursorState(
                    platform=PLATFORM,
                    scope=backfill_scope(room_id),
                    newest_external_id=token,
                    backfill_complete=complete,
                )
            )

    next_batch = payload.get("next_batch")
    if isinstance(next_batch, str) and next_batch:
        await store.put_cursor(
            CursorState(
                platform=PLATFORM,
                scope=SYNC_SCOPE,
                newest_external_id=next_batch,
                newest_at=datetime.datetime.now(datetime.UTC),
                backfill_complete=True,
            )
        )
    return result


class HttpxMatrixTransport:
    """Sync and messages over httpx. No calls happen until a method runs."""

    def __init__(
        self,
        homeserver: str,
        access_token: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base = homeserver.rstrip("/")
        self.access_token = access_token
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    def sync(self, since: str | None) -> dict[str, Any]:
        """``GET /_matrix/client/v3/sync`` with ``timeout=0``."""
        params: dict[str, str | int] = {"timeout": 0}
        if since:
            params["since"] = since
        response = self._client.get(
            f"{self.base}/_matrix/client/v3/sync",
            params=params,
            headers=self._headers(),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("Matrix /sync did not return an object")
        return payload

    def messages(self, room_id: str, from_token: str, limit: int) -> dict[str, Any]:
        """``GET /messages?dir=b``. The room id is percent-encoded."""
        encoded = quote(room_id, safe="")
        response = self._client.get(
            f"{self.base}/_matrix/client/v3/rooms/{encoded}/messages",
            params={"from": from_token, "dir": "b", "limit": limit},
            headers=self._headers(),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("Matrix /messages did not return an object")
        return payload

    def close(self) -> None:
        """Close the client when this transport created it."""
        if self._owns_client:
            self._client.close()
