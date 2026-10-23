"""IMAP backfill: UID pages, Message-ID dedup, order, owner mail."""

from __future__ import annotations

import datetime
import pytest

from src.connectors.imap_mail import (
    ImapMessage,
    ImaplibTransport,
    parse_rfc822,
    parse_uid_checkpoint,
    select_uids,
    sync_imap,
)
from src.core.sync.ingest import IngestService
from src.core.sync.memory_store import InMemoryMessageStore

UTC = datetime.UTC


def _msg(
    uid: int,
    when: datetime.datetime,
    body: str,
    *,
    mailbox: str = "INBOX",
    uidvalidity: int = 1,
    message_id: str | None = None,
    from_addr: str = "ada@example.com",
    subject: str = "Hello",
) -> ImapMessage:
    return ImapMessage(
        mailbox=mailbox,
        uidvalidity=uidvalidity,
        uid=uid,
        message_id=message_id or f"id-{uid}@example.com",
        sent_at=when,
        from_addr=from_addr,
        from_name="Ada",
        subject=subject,
        body=body,
    )


class MemoryImap:
    """In-process transport. Records which UIDs were fetched."""

    def __init__(self, boxes: dict[str, tuple[int, list[ImapMessage]]]) -> None:
        self.boxes = boxes
        self.fetched: list[tuple[str, list[int]]] = []

    def uidvalidity(self, mailbox: str) -> int:
        return self.boxes[mailbox][0]

    def search_uids(self, mailbox: str) -> list[int]:
        return [msg.uid for msg in self.boxes[mailbox][1]]

    def fetch(self, mailbox: str, uids: list[int]) -> list[ImapMessage]:
        self.fetched.append((mailbox, list(uids)))
        by_uid = {msg.uid: msg for msg in self.boxes[mailbox][1]}
        return [by_uid[uid] for uid in uids if uid in by_uid]


def test_select_uids_walks_backward_then_stops() -> None:
    uids = list(range(1, 11))
    chosen, token, done = select_uids(
        uids,
        uidvalidity=3,
        checkpoint=None,
        backfill_complete=False,
        max_messages=4,
    )
    assert chosen == [7, 8, 9, 10]
    assert done is False
    assert parse_uid_checkpoint(token) == (3, 10, 7)

    chosen, token, done = select_uids(
        uids,
        uidvalidity=3,
        checkpoint=token,
        backfill_complete=False,
        max_messages=4,
    )
    assert chosen == [3, 4, 5, 6]
    assert done is False

    chosen, token, done = select_uids(
        uids,
        uidvalidity=3,
        checkpoint=token,
        backfill_complete=False,
        max_messages=4,
    )
    assert chosen == [1, 2]
    assert done is True

    chosen, token, done = select_uids(
        uids + [11],
        uidvalidity=3,
        checkpoint=token,
        backfill_complete=True,
        max_messages=4,
    )
    assert chosen == [11]
    assert done is True


def test_select_uids_resets_when_uidvalidity_changes() -> None:
    chosen, _token, done = select_uids(
        [1, 2, 3],
        uidvalidity=9,
        checkpoint="4:3:1",
        backfill_complete=True,
        max_messages=2,
    )
    assert chosen == [2, 3]
    assert done is False


@pytest.mark.asyncio
async def test_backfill_is_idempotent_and_orders_by_date() -> None:
    early = datetime.datetime(2024, 1, 1, tzinfo=UTC)
    late = datetime.datetime(2024, 6, 1, tzinfo=UTC)
    # Higher UID has the earlier date, so insertion order is not send order.
    transport = MemoryImap(
        {
            "INBOX": (
                1,
                [
                    _msg(1, late, "second"),
                    _msg(2, early, "first"),
                ],
            )
        }
    )
    store = InMemoryMessageStore()
    first = await sync_imap(
        transport,
        store,
        mailboxes=["INBOX"],
        owner_address="me@example.com",
        max_messages=10,
    )
    assert first.inserted == 2
    ordered = store.list_chat("imap", "INBOX")
    assert [row.text for row in ordered] == [
        "Subject: Hello\n\nfirst",
        "Subject: Hello\n\nsecond",
    ]
    assert ordered[0].timestamp == early

    again = await sync_imap(
        transport,
        store,
        mailboxes=["INBOX"],
        owner_address="me@example.com",
        max_messages=10,
    )
    assert again.inserted == 0
    assert again.duplicates == 0
    assert len(store.rows) == 2
    assert len(transport.fetched) == 1


@pytest.mark.asyncio
async def test_rerun_of_same_page_inserts_nothing() -> None:
    """A crash before the UID cursor is saved still dedupes on Message-ID."""
    transport = MemoryImap(
        {"INBOX": (1, [_msg(5, datetime.datetime(2024, 2, 2, tzinfo=UTC), "only")])}
    )
    store = InMemoryMessageStore()
    real_put = store.put_cursor

    async def drop_uid_cursor(cursor):
        if cursor.scope.startswith("uid:"):
            return None
        return await real_put(cursor)

    store.put_cursor = drop_uid_cursor  # type: ignore[method-assign]
    first = await sync_imap(
        transport,
        store,
        mailboxes=["INBOX"],
        owner_address="me@example.com",
        max_messages=10,
    )
    second = await sync_imap(
        transport,
        store,
        mailboxes=["INBOX"],
        owner_address="me@example.com",
        max_messages=10,
    )
    assert first.inserted == 1
    assert second.inserted == 0
    assert second.duplicates == 1
    assert len(store.rows) == 1


@pytest.mark.asyncio
async def test_pages_drain_a_long_mailbox_without_duplicates() -> None:
    messages = [
        _msg(uid, datetime.datetime(2020, 1, uid, tzinfo=UTC), f"m{uid}")
        for uid in range(1, 8)
    ]
    transport = MemoryImap({"INBOX": (4, messages)})
    store = InMemoryMessageStore()
    total_inserted = 0
    for _ in range(5):
        result = await sync_imap(
            transport,
            store,
            mailboxes=["INBOX"],
            owner_address="",
            max_messages=3,
        )
        total_inserted += result.inserted
    assert total_inserted == 7
    assert len(store.rows) == 7
    final = await sync_imap(
        transport,
        store,
        mailboxes=["INBOX"],
        owner_address="",
        max_messages=3,
    )
    assert final.inserted == 0
    cursor = await store.get_cursor("imap", "uid:INBOX")
    assert cursor is not None
    assert cursor.backfill_complete is True


@pytest.mark.asyncio
async def test_uidvalidity_change_does_not_duplicate() -> None:
    when = datetime.datetime(2024, 3, 3, tzinfo=UTC)
    transport = MemoryImap(
        {"INBOX": (1, [_msg(1, when, "same", message_id="stable@x")])}
    )
    store = InMemoryMessageStore()
    await sync_imap(
        transport,
        store,
        mailboxes=["INBOX"],
        owner_address="",
        max_messages=10,
    )
    transport.boxes["INBOX"] = (
        99,
        [_msg(7, when, "same", message_id="stable@x", uidvalidity=99)],
    )
    again = await sync_imap(
        transport,
        store,
        mailboxes=["INBOX"],
        owner_address="",
        max_messages=10,
    )
    assert again.inserted == 0
    assert len(store.rows) == 1


@pytest.mark.asyncio
async def test_sent_and_owner_address_are_marked() -> None:
    when = datetime.datetime(2024, 4, 4, tzinfo=UTC)
    transport = MemoryImap(
        {
            "INBOX": (
                1,
                [
                    _msg(1, when, "from me", from_addr="me@example.com"),
                    _msg(2, when, "from ada", from_addr="ada@example.com"),
                ],
            ),
            "Sent": (
                1,
                [
                    _msg(
                        3,
                        when,
                        "outbound",
                        mailbox="Sent",
                        from_addr="me@example.com",
                    )
                ],
            ),
        }
    )
    store = InMemoryMessageStore()
    await sync_imap(
        transport,
        store,
        mailboxes=["INBOX", "Sent"],
        owner_address="me@example.com",
        max_messages=10,
    )
    inbox = {row.external_message_id: row for row in store.list_chat("imap", "INBOX")}
    assert inbox["id-1@example.com"].is_from_owner is True
    assert inbox["id-2@example.com"].is_from_owner is False
    sent = store.list_chat("imap", "Sent")
    assert sent[0].is_from_owner is True


@pytest.mark.asyncio
async def test_bad_mailbox_does_not_block_the_next() -> None:
    class Boom(MemoryImap):
        def uidvalidity(self, mailbox: str) -> int:
            if mailbox == "Broken":
                raise RuntimeError("select failed")
            return super().uidvalidity(mailbox)

    when = datetime.datetime(2024, 5, 5, tzinfo=UTC)
    transport = Boom({"Broken": (1, []), "INBOX": (1, [_msg(1, when, "ok")])})
    store = InMemoryMessageStore()
    result = await sync_imap(
        transport,
        store,
        mailboxes=["Broken", "INBOX"],
        owner_address="",
        max_messages=10,
    )
    assert result.inserted == 1


@pytest.mark.asyncio
async def test_ingest_is_chunked(monkeypatch) -> None:
    sizes: list[int] = []
    original = IngestService.ingest

    async def wrapped(self, records, **kwargs):
        sizes.append(len(records))
        return await original(self, records, **kwargs)

    monkeypatch.setattr(IngestService, "ingest", wrapped)
    messages = [
        _msg(uid, datetime.datetime(2021, 1, uid, tzinfo=UTC), f"b{uid}")
        for uid in range(1, 6)
    ]
    store = InMemoryMessageStore()
    result = await sync_imap(
        MemoryImap({"INBOX": (1, messages)}),
        store,
        mailboxes=["INBOX"],
        owner_address="",
        max_messages=10,
        chunk_size=2,
    )
    assert result.inserted == 5
    assert sizes == [2, 2, 1]


def test_parse_rfc822_prefers_plain_and_keeps_message_id() -> None:
    raw = (
        b"Subject: =?utf-8?q?Caf=C3=A9?=\r\n"
        b"From: Ada Lovelace <ada@example.com>\r\n"
        b"Date: Tue, 15 Oct 2024 12:00:00 +0000\r\n"
        b"Message-ID: <cafe@example.com>\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Hello there\r\n"
    )
    parsed = parse_rfc822(raw, mailbox="INBOX", uid=4, uidvalidity=2)
    assert parsed.message_id == "cafe@example.com"
    assert parsed.subject == "Café"
    assert parsed.body == "Hello there"
    assert parsed.from_addr == "ada@example.com"
    assert parsed.sent_at == datetime.datetime(2024, 10, 15, 12, 0, tzinfo=UTC)


def test_parse_rfc822_html_only() -> None:
    raw = (
        b"Subject: Hi\r\nFrom: a@b.c\r\n"
        b"Date: Tue, 15 Oct 2024 12:00:00 +0000\r\n"
        b"Content-Type: text/html\r\n\r\n"
        b"<p>Hello&nbsp;<b>there</b></p>"
    )
    parsed = parse_rfc822(raw, mailbox="INBOX", uid=1, uidvalidity=1)
    assert "Hello" in parsed.body
    assert "there" in parsed.body
    assert "<b>" not in parsed.body


def test_imaplib_transport_parses_fetch_without_network() -> None:
    raw = (
        b"Subject: Hi\r\nFrom: Bob <bob@ex.com>\r\n"
        b"Date: Wed, 16 Oct 2024 08:00:00 +0000\r\n"
        b"Message-ID: <m@ex.com>\r\n\r\nBody\r\n"
    )

    class FakeImap:
        def select(self, mailbox, readonly=True):
            del mailbox, readonly
            return "OK", [b"1"]

        def response(self, code):
            assert code == "UIDVALIDITY"
            return "OK", [b"7"]

        def uid(self, cmd, *args):
            del args
            if cmd == "SEARCH":
                return "OK", [b"4 9"]
            return "OK", [(b"1 (UID 9 BODY[] {10}", raw), b")"]

        def logout(self):
            return "OK", [b""]

    transport = ImaplibTransport("localhost", "u", "p", imap=FakeImap())
    assert transport.search_uids("INBOX") == [4, 9]
    fetched = transport.fetch("INBOX", [9])
    assert fetched[0].message_id == "m@ex.com"
    assert fetched[0].body == "Body"
    assert fetched[0].uidvalidity == 7
