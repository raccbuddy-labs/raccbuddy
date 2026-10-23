"""History ingest: the live-only bug, dedup, order, edits, and catch-up."""

from __future__ import annotations

import datetime
import os
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.sync.ingest import IngestService
from src.core.sync.keys import stable_chat_id
from src.core.sync.memory_store import InMemoryMessageStore
from src.core.sync.plan import plan_incremental
from src.core.sync.telegram_export import parse_telegram_export_owned
from src.core.sync.types import CursorState, IncomingRecord, IngestResult, StoredMessage
from src.handlers.chat import chat_handler, edited_message_handler

UTC = datetime.UTC


def _rec(
    message_id: str,
    when: datetime.datetime,
    text: str,
    *,
    chat: str = "15551212@c.us",
    edited_at: datetime.datetime | None = None,
) -> IncomingRecord:
    return (
        IncomingRecord(
            platform="whatsapp",
            external_chat_id=chat,
            external_message_id=message_id,
            text=text,
            timestamp=when,
            sender_handle="15551212",
            sender_name="Ada",
        )
        if edited_at is None
        else IncomingRecord(
            platform="whatsapp",
            external_chat_id=chat,
            external_message_id=message_id,
            text=text,
            timestamp=when,
            sender_handle="15551212",
            sender_name="Ada",
            edited_at=edited_at,
        )
    )


def test_builtin_hash_changes_across_processes() -> None:
    """The old chat id was Python's salted hash(). Prove that salt exists."""
    code = "print(hash('15551212@c.us') & 0x7FFFFFFFFFFFFFFF)"
    env_base = os.environ.copy()

    def _run(seed: str) -> str:
        env = dict(env_base)
        env["PYTHONHASHSEED"] = seed
        return subprocess.check_output(
            [sys.executable, "-c", code],
            env=env,
            text=True,
        ).strip()

    assert _run("1") != _run("2")


def test_stable_chat_id_is_pure_and_keeps_telegram_ids() -> None:
    first = stable_chat_id("whatsapp", "15551212@c.us")
    second = stable_chat_id("whatsapp", "15551212@c.us")
    assert first == second
    assert first > 0
    assert stable_chat_id("whatsapp", "other@c.us") != first
    assert stable_chat_id("telegram", "200") == 200
    assert stable_chat_id("telegram", "-100123") == -100123


@pytest.mark.asyncio
async def test_live_listener_misses_history_until_backfill() -> None:
    """Reproduce the bug: events after connect are not the chat history.

    A listener that only forwards messages newer than its start time stores
    one row. Backfill of the same provider page restores the older ones in
    send order, and running that backfill again inserts nothing.
    """
    started = datetime.datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
    history = [
        _rec("m1", started - datetime.timedelta(days=2), "two days ago"),
        _rec("m2", started - datetime.timedelta(days=1), "yesterday"),
        _rec("m3", started + datetime.timedelta(minutes=5), "just now"),
    ]
    live_only = [msg for msg in history if msg.timestamp >= started]
    assert len(live_only) == 1

    store = InMemoryMessageStore()
    service = IngestService(store)
    await service.ingest(live_only)
    assert len(store.rows) == 1

    plan = plan_incremental(history, cursor=None)
    first = await service.ingest(plan.to_send, history_complete=True)
    assert first.inserted == 2
    assert len(store.rows) == 3
    assert [
        row.external_message_id for row in store.list_chat("whatsapp", "15551212@c.us")
    ] == [
        "m1",
        "m2",
        "m3",
    ]

    second = await service.ingest(plan.to_send, history_complete=True)
    assert second.inserted == 0
    assert second.updated == 0
    assert second.duplicates == 3
    assert len(store.rows) == 3


@pytest.mark.asyncio
async def test_out_of_order_arrival_sorts_by_original_timestamp() -> None:
    store = InMemoryMessageStore()
    service = IngestService(store)
    base = datetime.datetime(2026, 1, 1, tzinfo=UTC)
    await service.ingest([_rec("2", base + datetime.timedelta(hours=2), "second")])
    await service.ingest([_rec("1", base, "first")])
    await service.ingest([_rec("3", base + datetime.timedelta(hours=1), "middle")])
    assert [row.text for row in store.list_chat("whatsapp", "15551212@c.us")] == [
        "first",
        "middle",
        "second",
    ]


@pytest.mark.asyncio
async def test_edit_updates_body_and_keeps_original_timestamp() -> None:
    store = InMemoryMessageStore()
    service = IngestService(store)
    sent = datetime.datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
    edited = sent + datetime.timedelta(hours=3)
    await service.ingest([_rec("1", sent, "hello")])
    result = await service.ingest([_rec("1", sent, "hello!", edited_at=edited)])
    assert result.inserted == 0
    assert result.updated == 1
    row = store.list_chat("whatsapp", "15551212@c.us")[0]
    assert row.text == "hello!"
    assert row.timestamp == sent
    assert row.edited_at == edited
    assert len(store.rows) == 1


@pytest.mark.asyncio
async def test_cursor_moves_forward_only_and_resume_skips_overlap() -> None:
    store = InMemoryMessageStore()
    service = IngestService(store)
    base = datetime.datetime(2026, 5, 1, tzinfo=UTC)
    page = [
        _rec("1", base, "a"),
        _rec("2", base + datetime.timedelta(hours=1), "b"),
    ]
    await service.ingest(page, history_complete=False)
    cursor = await store.get_cursor("whatsapp", "15551212@c.us")
    assert cursor is not None
    assert cursor.newest_external_id == "2"
    assert cursor.backfill_complete is False

    # An older page must not drag the watermark backward.
    await service.ingest([_rec("0", base - datetime.timedelta(days=1), "older")])
    cursor = await store.get_cursor("whatsapp", "15551212@c.us")
    assert cursor is not None
    assert cursor.newest_external_id == "2"

    later = _rec("3", base + datetime.timedelta(hours=5), "c")
    plan = plan_incremental([*page, later], cursor)
    assert [msg.external_message_id for msg in plan.to_send] == ["2", "3"] or "3" in [
        msg.external_message_id for msg in plan.to_send
    ]
    assert plan.gap is False
    resumed = await service.ingest(plan.to_send, history_complete=True)
    assert resumed.inserted == 1
    cursor = await store.get_cursor("whatsapp", "15551212@c.us")
    assert cursor is not None
    assert cursor.backfill_complete is True
    assert cursor.newest_external_id == "3"


@pytest.mark.asyncio
async def test_gapped_page_does_not_mark_backfill_complete() -> None:
    """A short page newer than the cursor must not freeze the hole."""
    store = InMemoryMessageStore()
    service = IngestService(store)
    base = datetime.datetime(2026, 6, 1, tzinfo=UTC)
    await service.ingest(
        [_rec("old", base, "kept")],
        history_complete=False,
        gap_blocks_complete=True,
    )
    page = [
        _rec("n1", base + datetime.timedelta(days=10), "x"),
        _rec("n2", base + datetime.timedelta(days=11), "y"),
    ]
    await service.ingest(page, history_complete=True, gap_blocks_complete=True)
    cursor = await store.get_cursor("whatsapp", "15551212@c.us")
    assert cursor is not None
    assert cursor.backfill_complete is False
    assert cursor.newest_external_id == "n2"


@pytest.mark.asyncio
async def test_backfill_adopts_legacy_row_instead_of_duplicating() -> None:
    """Pre-sync rows have no provider id. Replaying history fills that id."""
    sent = datetime.datetime(2024, 5, 1, 12, 0, tzinfo=UTC)
    store = InMemoryMessageStore()
    store.legacy.append(
        StoredMessage(
            id=7,
            platform="whatsapp",
            external_chat_id="15551212@c.us",
            external_message_id="",
            chat_id=111,
            text="hello",
            timestamp=sent,
            is_from_owner=False,
            contact_id=4,
        )
    )
    service = IngestService(store)
    result = await service.ingest(
        [
            IncomingRecord(
                platform="whatsapp",
                external_chat_id="15551212@c.us",
                external_message_id="true_ABCDEF",
                text="hello",
                timestamp=sent,
                sender_handle="15551212",
                is_from_owner=True,
                contact_id=4,
            )
        ]
    )
    assert result.inserted == 0
    assert result.updated == 1
    assert store.legacy == []
    row = store.list_chat("whatsapp", "15551212@c.us")[0]
    assert row.external_message_id == "true_ABCDEF"
    assert row.timestamp == sent
    assert row.is_from_owner is True
    assert row.chat_id == stable_chat_id("whatsapp", "15551212@c.us")
    assert len(store.rows) == 1

    again = await service.ingest(
        [
            IncomingRecord(
                platform="whatsapp",
                external_chat_id="15551212@c.us",
                external_message_id="true_ABCDEF",
                text="hello",
                timestamp=sent,
                is_from_owner=True,
                contact_id=4,
            )
        ]
    )
    assert again.inserted == 0
    assert again.duplicates == 1
    assert len(store.rows) == 1


@pytest.mark.asyncio
async def test_backfill_adopts_unmapped_group_row_by_contact() -> None:
    sent = datetime.datetime(2024, 8, 1, 8, 0, tzinfo=UTC)
    store = InMemoryMessageStore()
    store.legacy.append(
        StoredMessage(
            id=3,
            platform="whatsapp",
            external_chat_id="",
            external_message_id="",
            chat_id=999,
            text="in the group",
            timestamp=sent,
            contact_id=4,
        )
    )
    service = IngestService(store)
    result = await service.ingest(
        [
            IncomingRecord(
                platform="whatsapp",
                external_chat_id="family@g.us",
                external_message_id="grp1",
                text="in the group",
                timestamp=sent,
                contact_id=4,
                is_from_owner=False,
            )
        ]
    )
    assert result.inserted == 0
    assert result.updated == 1
    row = store.rows[("whatsapp", "family@g.us", "grp1")]
    assert row.chat_id == stable_chat_id("whatsapp", "family@g.us")
    assert row.timestamp == sent
    assert store.legacy == []


def test_incremental_plan_flags_a_gap_when_the_page_misses_the_cursor() -> None:
    base = datetime.datetime(2026, 6, 1, tzinfo=UTC)
    cursor = CursorState(
        platform="whatsapp",
        scope="chat",
        newest_external_id="old",
        newest_at=base,
    )
    page = [
        _rec("n1", base + datetime.timedelta(days=10), "x", chat="chat"),
        _rec("n2", base + datetime.timedelta(days=11), "y", chat="chat"),
    ]
    plan = plan_incremental(page, cursor)
    assert plan.gap is True
    assert len(plan.to_send) == 2


def test_telegram_export_uses_original_ids_and_replays_cleanly() -> None:
    payload = {
        "name": "Ada",
        "id": 4242,
        "messages": [
            {"id": 1, "type": "service", "date": "2024-01-01T00:00:00", "text": ""},
            {
                "id": 2,
                "type": "message",
                "date": "2024-01-02T03:04:05",
                "date_unixtime": "1704164645",
                "from": "Ada",
                "from_id": "user9",
                "text": ["hello", {"type": "plain", "text": " there"}],
            },
            {
                "id": 3,
                "type": "message",
                "date": "2024-01-03T00:00:00",
                "from": "Me",
                "from_id": "user1",
                "text": "ping",
                "edited": "2024-01-03T01:00:00",
            },
        ],
    }
    records = parse_telegram_export_owned(payload, owner_from_id="user1")
    assert [rec.external_message_id for rec in records] == ["2", "3"]
    assert records[0].text == "hello there"
    assert records[0].external_chat_id == "4242"
    assert records[0].is_from_owner is False
    assert records[1].is_from_owner is True
    assert records[1].edited_at is not None
    assert records[0].timestamp == datetime.datetime.fromtimestamp(1704164645, tz=UTC)


@pytest.mark.asyncio
async def test_telegram_export_backfill_is_idempotent() -> None:
    records = parse_telegram_export_owned(
        {
            "id": 7,
            "messages": [
                {
                    "id": 1,
                    "type": "message",
                    "date": "2024-02-01T00:00:00",
                    "from_id": "user2",
                    "text": "hey",
                }
            ],
        },
        owner_from_id=None,
    )
    store = InMemoryMessageStore()
    service = IngestService(store)
    first = await service.ingest(records, history_complete=True)
    second = await service.ingest(records, history_complete=True)
    assert first.inserted == 1
    assert second.inserted == 0
    assert second.duplicates == 1
    assert len(store.rows) == 1


@pytest.mark.asyncio
async def test_redelivered_telegram_update_does_not_call_the_model() -> None:
    update = MagicMock()
    update.message.text = "Hello"
    update.message.message_id = 42
    update.message.date = datetime.datetime(2026, 10, 1, tzinfo=UTC)
    update.message.forward_origin = None
    update.message.reply_text = AsyncMock()
    update.effective_user.id = 100
    update.effective_user.first_name = "Owner"
    update.effective_chat.id = 200

    with (
        patch(
            "src.handlers.chat.reject_non_owner",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch("src.handlers.chat._owner_id", return_value=100),
        patch(
            "src.handlers.chat.persist_incoming",
            new_callable=AsyncMock,
            return_value=IngestResult(inserted=0, duplicates=1),
        ),
        patch("src.handlers.chat.generate_chat", new_callable=AsyncMock) as generate,
    ):
        await chat_handler(update, MagicMock())

    generate.assert_not_called()
    update.message.reply_text.assert_not_called()


@pytest.mark.asyncio
async def test_telegram_edit_is_ingested_with_the_same_message_id() -> None:
    update = MagicMock()
    update.edited_message.text = "Hello v2"
    update.edited_message.message_id = 42
    update.edited_message.date = datetime.datetime(2026, 10, 1, tzinfo=UTC)
    update.edited_message.edit_date = datetime.datetime(2026, 10, 1, 1, tzinfo=UTC)
    update.effective_user.id = 100
    update.effective_chat.id = 200
    update.message = None

    with (
        patch(
            "src.handlers.chat.reject_non_owner",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch("src.handlers.chat._owner_id", return_value=100),
        patch("src.handlers.chat.persist_incoming", new_callable=AsyncMock) as persist,
    ):
        await edited_message_handler(update, MagicMock())

    record = persist.await_args.args[0][0]
    assert record.external_message_id == "42"
    assert record.text == "Hello v2"
    assert record.edited_at == datetime.datetime(2026, 10, 1, 1, tzinfo=UTC)
