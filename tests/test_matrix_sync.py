"""Matrix backfill: /sync cursor, /messages pages, dedup, edits, encryption."""

from __future__ import annotations

import datetime
from typing import Any

import httpx
import pytest

from src.connectors.matrix_chat import (
    HttpxMatrixTransport,
    parse_event,
    room_is_encrypted,
    sync_matrix,
)
from src.core.sync.memory_store import InMemoryMessageStore

ROOM = "!r:example.org"
OWNER = "@me:example.org"


def _text(
    event_id: str,
    ts_ms: int,
    body: str,
    *,
    sender: str = "@ada:example.org",
) -> dict[str, Any]:
    return {
        "type": "m.room.message",
        "event_id": event_id,
        "sender": sender,
        "origin_server_ts": ts_ms,
        "content": {"msgtype": "m.text", "body": body},
    }


class FakeMatrix:
    """Scripted /sync and /messages. No network."""

    def __init__(
        self,
        sync_payload: dict[str, Any],
        pages: dict[str, dict[str, Any]],
    ) -> None:
        self.sync_payload = sync_payload
        self.pages = pages
        self.calls: list[tuple] = []

    def sync(self, since: str | None) -> dict[str, Any]:
        self.calls.append(("sync", since))
        return self.sync_payload

    def messages(self, room_id: str, from_token: str, limit: int) -> dict[str, Any]:
        self.calls.append(("messages", room_id, from_token, limit))
        page = self.pages[from_token]
        chunk = list(page["chunk"])[:limit]
        return {"chunk": chunk, "end": page.get("end")}


def _room(events: list[dict[str, Any]], prev: str | None, members: int = 2) -> dict:
    timeline: dict[str, Any] = {"events": events}
    if prev is not None:
        timeline["prev_batch"] = prev
    return {
        "summary": {"m.joined_member_count": members},
        "timeline": timeline,
    }


@pytest.mark.asyncio
async def test_history_pages_then_replay_inserts_nothing() -> None:
    pages = {
        "t0": {
            "chunk": [_text("$2", 2000, "two"), _text("$1", 1000, "one")],
            "end": "t1",
        },
        "t1": {"chunk": [_text("$3", 3000, "three")], "end": None},
    }
    payload = {
        "next_batch": "s1",
        "rooms": {"join": {ROOM: _room([], "t0")}},
    }
    transport = FakeMatrix(payload, pages)
    store = InMemoryMessageStore()
    first = await sync_matrix(
        transport,
        store,
        owner_mxid=OWNER,
        max_per_room=2,
        page_size=2,
    )
    assert first.inserted == 2
    cursor = await store.get_cursor("matrix", f"backfill:{ROOM}")
    assert cursor is not None
    assert cursor.backfill_complete is False
    assert cursor.newest_external_id == "t1"

    second = await sync_matrix(
        transport,
        store,
        owner_mxid=OWNER,
        max_per_room=2,
        page_size=2,
    )
    assert second.inserted == 1
    done = await store.get_cursor("matrix", f"backfill:{ROOM}")
    assert done is not None
    assert done.backfill_complete is True

    third = await sync_matrix(
        transport,
        store,
        owner_mxid=OWNER,
        max_per_room=2,
        page_size=2,
    )
    assert third.inserted == 0
    assert third.duplicates == 0
    message_calls = [call for call in transport.calls if call[0] == "messages"]
    # Two backfill runs, then the finished run must not paginate.
    assert len(message_calls) == 2
    assert [call[2] for call in message_calls] == ["t0", "t1"]
    ordered = store.list_chat("matrix", ROOM)
    assert [row.text for row in ordered] == ["one", "two", "three"]
    assert len(store.rows) == 3


@pytest.mark.asyncio
async def test_sync_token_resumes_and_owner_flag() -> None:
    payload = {
        "next_batch": "s2",
        "rooms": {
            "join": {
                ROOM: _room(
                    [_text("$9", 9000, "mine", sender=OWNER)],
                    prev=None,
                )
            }
        },
    }
    transport = FakeMatrix(payload, {})
    store = InMemoryMessageStore()
    await sync_matrix(transport, store, owner_mxid=OWNER)
    assert transport.calls[0] == ("sync", None)
    row = store.list_chat("matrix", ROOM)[0]
    assert row.is_from_owner is True
    await sync_matrix(transport, store, owner_mxid=OWNER)
    assert transport.calls[-1] == ("sync", "s2") or ("sync", "s2") in transport.calls
    assert ("sync", "s2") in transport.calls


@pytest.mark.asyncio
async def test_edit_keeps_original_timestamp() -> None:
    original = _text("$a", 1_000, "hi")
    edited = {
        "type": "m.room.message",
        "event_id": "$edit",
        "sender": "@ada:example.org",
        "origin_server_ts": 5_000,
        "content": {
            "msgtype": "m.text",
            "body": "* hi there",
            "m.new_content": {"msgtype": "m.text", "body": "hi there"},
            "m.relates_to": {"rel_type": "m.replace", "event_id": "$a"},
        },
    }
    store = InMemoryMessageStore()
    first = FakeMatrix(
        {"next_batch": "s1", "rooms": {"join": {ROOM: _room([original], None)}}},
        {},
    )
    await sync_matrix(first, store, owner_mxid=OWNER)
    second = FakeMatrix(
        {"next_batch": "s2", "rooms": {"join": {ROOM: _room([edited], None)}}},
        {},
    )
    result = await sync_matrix(second, store, owner_mxid=OWNER)
    assert result.updated == 1
    assert result.inserted == 0
    row = store.list_chat("matrix", ROOM)[0]
    assert row.text == "hi there"
    assert row.timestamp == datetime.datetime.fromtimestamp(1, tz=datetime.UTC)
    assert row.edited_at is not None
    assert len(store.rows) == 1


@pytest.mark.asyncio
async def test_out_of_order_timeline_sorts_by_origin_ts() -> None:
    events = [
        _text("$c", 3000, "c"),
        _text("$a", 1000, "a"),
        _text("$b", 2000, "b"),
    ]
    transport = FakeMatrix(
        {"next_batch": "s1", "rooms": {"join": {ROOM: _room(events, None)}}},
        {},
    )
    store = InMemoryMessageStore()
    await sync_matrix(transport, store, owner_mxid=OWNER)
    assert [row.text for row in store.list_chat("matrix", ROOM)] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_encrypted_room_is_not_fetched() -> None:
    room = _room([_text("$1", 1000, "secret")], "t0")
    room["state"] = {
        "events": [
            {
                "type": "m.room.encryption",
                "content": {"algorithm": "m.megolm.v1.aes-sha2"},
            }
        ]
    }
    transport = FakeMatrix(
        {"next_batch": "s1", "rooms": {"join": {ROOM: room}}},
        {"t0": {"chunk": [_text("$0", 500, "older")], "end": None}},
    )
    store = InMemoryMessageStore()
    result = await sync_matrix(transport, store, owner_mxid=OWNER)
    assert result.inserted == 0
    assert store.rows == {}
    assert not any(call[0] == "messages" for call in transport.calls)
    assert room_is_encrypted(room) is True


@pytest.mark.asyncio
async def test_group_flag_and_media_skipped() -> None:
    image = {
        "type": "m.room.message",
        "event_id": "$img",
        "sender": "@ada:example.org",
        "origin_server_ts": 1000,
        "content": {"msgtype": "m.image", "body": "pic.png"},
    }
    transport = FakeMatrix(
        {
            "next_batch": "s1",
            "rooms": {
                "join": {ROOM: _room([image, _text("$1", 2000, "hi")], None, members=4)}
            },
        },
        {},
    )
    store = InMemoryMessageStore()
    result = await sync_matrix(transport, store, owner_mxid=OWNER)
    assert result.inserted == 1
    assert result.skipped == 1
    record = parse_event(
        ROOM,
        _text("$1", 2000, "hi"),
        owner_mxid=OWNER,
        is_group=True,
    )
    assert record is not None
    assert record.is_group is True


def test_replace_without_target_is_skipped() -> None:
    event = {
        "type": "m.room.message",
        "event_id": "$e",
        "sender": "@a:example.org",
        "origin_server_ts": 10,
        "content": {"m.relates_to": {"rel_type": "m.replace"}},
    }
    assert parse_event(ROOM, event, owner_mxid=OWNER, is_group=False) is None


def test_httpx_transport_sends_bearer_and_encodes_room_id() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/sync"):
            return httpx.Response(200, json={"next_batch": "n1", "rooms": {}})
        return httpx.Response(200, json={"chunk": [], "end": None})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    transport = HttpxMatrixTransport(
        "https://matrix.example",
        "secret",
        client=client,
    )
    payload = transport.sync("s0")
    assert payload["next_batch"] == "n1"
    history = transport.messages(ROOM, "t0", 25)
    assert history["chunk"] == []
    assert seen[0].headers["authorization"] == "Bearer secret"
    assert seen[0].url.params["since"] == "s0"
    assert seen[0].url.params["timeout"] == "0"
    raw_path = seen[1].url.raw_path.decode()
    assert "/rooms/%21r%3Aexample.org/messages" in raw_path
    assert seen[1].url.params["dir"] == "b"
    assert seen[1].url.params["limit"] == "25"
    transport.close()
    client.close()
