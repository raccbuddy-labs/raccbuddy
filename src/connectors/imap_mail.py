"""IMAP history sync.

The mailbox is the chat. Each message is keyed by its ``Message-ID``
(or ``uid:{uidvalidity}:{uid}`` when the header is missing), so fetching
the same UID again does not insert a second row.

IMAP UIDs are only meaningful inside one UIDVALIDITY generation. The
checkpoint for a mailbox lives on a separate cursor scope (``uid:{name}``)
from the message watermark, because the message watermark stores the
Message-ID, not the UID.

One run fetches at most ``max_messages`` bodies. New UIDs above the high
watermark come first. Remaining budget walks backward from the lowest UID
already fetched, so a large mailbox drains across runs instead of stopping
at the newest window. ``backfill_complete`` flips true once that walk
reaches the oldest UID in the mailbox.

Limits, documented on purpose:

* The server must still have the message. Expunged mail is gone.
* ``UID SEARCH ALL`` downloads the UID list, not the bodies. Bodies are
  capped per run.
* A UIDVALIDITY change invalidates the checkpoint. The next run re-reads
  from the newest window. Message-IDs still dedupe mail already stored.
* ``BODY.PEEK`` is used so a sync does not set ``\\Seen``.
* Mailboxes whose names are in the sent set are stored as owner-sent.
  So is any message whose From address matches the owner address.
"""

from __future__ import annotations

import asyncio
import datetime
import email
import imaplib
import logging
import re
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from typing import Protocol

from src.connectors.common import add_results, ingest_chunks
from src.core.sync.ingest import IngestService
from src.core.sync.keys import ensure_utc
from src.core.sync.types import CursorState, IncomingRecord, IngestResult

logger = logging.getLogger(__name__)

PLATFORM = "imap"
_UID_FETCH_BATCH = 50
_DEFAULT_SENT = frozenset(
    {"sent", "sent items", "sent mail", "[gmail]/sent mail"},
)


class ImapMailboxError(RuntimeError):
    """SELECT or FETCH failed for one mailbox."""


@dataclass
class ImapMessage:
    """One RFC822 message already pulled off the wire."""

    mailbox: str
    uidvalidity: int
    uid: int
    message_id: str | None
    sent_at: datetime.datetime | None
    from_addr: str
    from_name: str | None
    subject: str
    body: str


@dataclass
class ImapPage:
    """UIDs chosen for this run, plus the checkpoint to store after ingest."""

    messages: list[ImapMessage]
    checkpoint: str
    history_complete: bool


class ImapTransport(Protocol):
    """The slice of IMAP the sync uses. Tests supply a fake."""

    def uidvalidity(self, mailbox: str) -> int:
        """UIDVALIDITY of *mailbox*."""
        ...

    def search_uids(self, mailbox: str) -> list[int]:
        """Every UID currently in *mailbox*."""
        ...

    def fetch(self, mailbox: str, uids: list[int]) -> list[ImapMessage]:
        """RFC822 for *uids*. Missing UIDs are omitted."""
        ...


def uid_scope(mailbox: str) -> str:
    """Cursor scope that stores the UID watermark, not message rows."""
    return f"uid:{mailbox}"


def parse_uid_checkpoint(value: str | None) -> tuple[int, int, int] | None:
    """Parse ``{uidvalidity}:{high}:{low}`` or return None."""
    if not value:
        return None
    parts = value.split(":")
    if len(parts) != 3:
        return None
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None


def format_uid_checkpoint(uidvalidity: int, high: int, low: int) -> str:
    """Encode the UID watermark."""
    return f"{uidvalidity}:{high}:{low}"


def select_uids(
    all_uids: list[int],
    *,
    uidvalidity: int,
    checkpoint: str | None,
    backfill_complete: bool,
    max_messages: int,
) -> tuple[list[int], str, bool]:
    """Choose which UIDs to fetch this run.

    Returns ``(uids, checkpoint, history_complete)``. *checkpoint* is safe
    to store before the next run. An empty mailbox is complete.
    """
    cap = max_messages if max_messages > 0 else 500
    uids = sorted({uid for uid in all_uids if uid > 0})
    parsed = parse_uid_checkpoint(checkpoint)
    if not uids:
        return [], format_uid_checkpoint(uidvalidity, 0, 0), True

    if parsed is None or parsed[0] != uidvalidity:
        chosen = uids[-cap:]
        complete = chosen[0] == uids[0]
        return (
            chosen,
            format_uid_checkpoint(uidvalidity, chosen[-1], chosen[0]),
            complete,
        )

    _, high, low = parsed
    newer = [uid for uid in uids if uid > high]
    older = [] if backfill_complete else [uid for uid in uids if uid < low]
    chosen_new = newer[:cap]
    remaining = cap - len(chosen_new)
    chosen_old = older[-remaining:] if remaining > 0 else []
    chosen = chosen_old + chosen_new
    if not chosen:
        return [], format_uid_checkpoint(uidvalidity, high, low), True

    new_high = max([high, *chosen_new]) if chosen_new else high
    new_low = min([low, *chosen_old]) if chosen_old else low
    reached_oldest = not any(uid < new_low for uid in uids)
    complete = backfill_complete or reached_oldest
    return chosen, format_uid_checkpoint(uidvalidity, new_high, new_low), complete


def pairs_from_fetch(data: list) -> list[tuple[int, bytes]]:
    """Pull ``(uid, rfc822)`` pairs out of an imaplib FETCH response."""
    found: list[tuple[int, bytes]] = []
    for item in data:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        meta, raw = item[0], item[1]
        if isinstance(meta, bytes):
            meta_text = meta.decode("utf-8", "replace")
        else:
            meta_text = str(meta)
        if not isinstance(raw, bytes | bytearray):
            continue
        match = re.search(r"\bUID\s+(\d+)", meta_text)
        if match is None:
            continue
        found.append((int(match.group(1)), bytes(raw)))
    return found


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (ValueError, IndexError):
        return value


def _decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        raw = part.get_payload()
        return raw if isinstance(raw, str) else ""
    if not isinstance(payload, bytes):
        return str(payload)
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _html_to_text(raw: str) -> str:
    import html as html_lib

    without_blocks = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw)
    without_tags = re.sub(r"(?s)<[^>]+>", " ", without_blocks)
    unescaped = html_lib.unescape(without_tags)
    return re.sub(r"\s+", " ", unescaped).strip()


def _body_text(msg: Message) -> str:
    if msg.is_multipart():
        plain: str | None = None
        html_body: str | None = None
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                continue
            kind = part.get_content_type()
            if kind == "text/plain" and plain is None:
                plain = _decode_part(part)
            elif kind == "text/html" and html_body is None:
                html_body = _decode_part(part)
        if plain is not None and plain.strip():
            return plain.strip()
        if html_body:
            return _html_to_text(html_body)
        return ""
    text = _decode_part(msg).strip()
    if msg.get_content_type() == "text/html":
        return _html_to_text(text)
    return text


def _parse_date(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    try:
        return ensure_utc(parsedate_to_datetime(value))
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def parse_rfc822(
    raw: bytes,
    *,
    mailbox: str,
    uid: int,
    uidvalidity: int,
) -> ImapMessage:
    """Parse one RFC822 payload. A missing Date leaves ``sent_at`` unset."""
    msg = email.message_from_bytes(raw)
    message_id = (msg.get("Message-ID") or "").strip().strip("<>") or None
    name, addr = parseaddr(_decode_header(msg.get("From")))
    return ImapMessage(
        mailbox=mailbox,
        uidvalidity=uidvalidity,
        uid=uid,
        message_id=message_id,
        sent_at=_parse_date(msg.get("Date")),
        from_addr=addr.strip(),
        from_name=name.strip() or None,
        subject=_decode_header(msg.get("Subject")).strip(),
        body=_body_text(msg),
    )


def _fit_id(value: str) -> str:
    """Keep provider ids inside the 512-char column."""
    if len(value) <= 512:
        return value
    import hashlib

    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def message_text(subject: str, body: str) -> str:
    """Store the subject with the body so a blank body still has context."""
    subject = subject.strip()
    body = body.strip()
    if subject and body:
        return f"Subject: {subject}\n\n{body}"
    if body:
        return body
    if subject:
        return f"Subject: {subject}"
    return ""


def to_record(
    msg: ImapMessage,
    *,
    owner_address: str,
    sent_mailboxes: frozenset[str] | set[str],
) -> IncomingRecord | None:
    """Map one fetched message onto the ingest contract.

    Returns None when there is no Date or no text. The UID checkpoint
    still advances, so a later run does not refetch it forever.
    """
    text = message_text(msg.subject, msg.body)
    if msg.sent_at is None or not text:
        return None
    external_id = msg.message_id or f"uid:{msg.uidvalidity}:{msg.uid}"
    sent_names = {name.lower() for name in sent_mailboxes}
    from_owner = msg.mailbox.lower() in sent_names
    if owner_address and msg.from_addr.lower() == owner_address.strip().lower():
        from_owner = True
    sender = msg.from_addr or msg.mailbox
    return IncomingRecord(
        platform=PLATFORM,
        external_chat_id=msg.mailbox,
        external_message_id=_fit_id(external_id),
        text=text,
        timestamp=msg.sent_at,
        sender_handle=sender,
        sender_name=msg.from_name or sender,
        is_from_owner=from_owner,
    )


def fetch_imap_page(
    transport: ImapTransport,
    mailbox: str,
    cursor: CursorState | None,
    *,
    max_messages: int,
) -> ImapPage:
    """SELECT, choose UIDs, fetch bodies. Blocking."""
    uidvalidity = transport.uidvalidity(mailbox)
    chosen, checkpoint, complete = select_uids(
        transport.search_uids(mailbox),
        uidvalidity=uidvalidity,
        checkpoint=cursor.newest_external_id if cursor else None,
        backfill_complete=bool(cursor and cursor.backfill_complete),
        max_messages=max_messages,
    )
    messages = transport.fetch(mailbox, chosen) if chosen else []
    return ImapPage(
        messages=messages,
        checkpoint=checkpoint,
        history_complete=complete,
    )


async def sync_imap(
    transport: ImapTransport,
    store,
    *,
    mailboxes: list[str],
    owner_address: str,
    sent_mailboxes: set[str] | None = None,
    max_messages: int = 500,
    chunk_size: int = 500,
) -> IngestResult:
    """Backfill and incrementally sync each mailbox.

    A mailbox that raises is logged and skipped. The others still run.
    Re-running after the cursors are stored fetches nothing new.
    """
    sent = sent_mailboxes if sent_mailboxes is not None else set(_DEFAULT_SENT)
    result = IngestResult()
    service = IngestService(store)
    for mailbox in mailboxes:
        try:
            cursor = await store.get_cursor(PLATFORM, uid_scope(mailbox))
            page = await asyncio.to_thread(
                fetch_imap_page,
                transport,
                mailbox,
                cursor,
                max_messages=max_messages,
            )
        except Exception:
            logger.exception("IMAP sync failed for mailbox %s", mailbox)
            continue
        records: list[IncomingRecord] = []
        for msg in page.messages:
            record = to_record(
                msg,
                owner_address=owner_address,
                sent_mailboxes=sent,
            )
            if record is None:
                result.skipped += 1
            else:
                records.append(record)
        part = await ingest_chunks(
            service,
            records,
            history_complete=page.history_complete,
            chunk_size=chunk_size,
        )
        add_results(result, part)
        await store.put_cursor(
            CursorState(
                platform=PLATFORM,
                scope=uid_scope(mailbox),
                newest_external_id=page.checkpoint,
                backfill_complete=page.history_complete,
            )
        )
    return result


class ImaplibTransport:
    """Blocking IMAP client. Call it from a worker thread in production.

    ``imap`` lets tests inject a connected fake. Leave it None to login.
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        port: int = 993,
        use_ssl: bool = True,
        timeout: int = 30,
        imap: imaplib.IMAP4 | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.use_ssl = use_ssl
        self.timeout = timeout
        self._imap = imap
        self._selected: str | None = None

    def _conn(self) -> imaplib.IMAP4:
        if self._imap is not None:
            return self._imap
        if self.use_ssl:
            client: imaplib.IMAP4 = imaplib.IMAP4_SSL(
                self.host,
                self.port,
                timeout=self.timeout,
            )
        else:
            client = imaplib.IMAP4(self.host, self.port, timeout=self.timeout)
        client.login(self.username, self.password)
        self._imap = client
        return client

    def _select(self, mailbox: str) -> None:
        if self._selected == mailbox:
            return
        client = self._conn()
        typ, _data = client.select(mailbox, readonly=True)
        if typ != "OK":
            raise ImapMailboxError(f"SELECT {mailbox} failed: {typ}")
        self._selected = mailbox

    def uidvalidity(self, mailbox: str) -> int:
        """UIDVALIDITY after SELECT."""
        self._select(mailbox)
        _typ, data = self._conn().response("UIDVALIDITY")
        if not data or data[0] is None:
            raise ImapMailboxError(f"UIDVALIDITY missing for {mailbox}")
        raw = data[0]
        if isinstance(raw, bytes):
            raw = raw.decode("ascii", "replace")
        return int(raw)

    def search_uids(self, mailbox: str) -> list[int]:
        """UID SEARCH ALL."""
        self._select(mailbox)
        typ, data = self._conn().uid("SEARCH", "ALL")
        if typ != "OK" or not data or data[0] is None:
            return []
        raw = data[0]
        if isinstance(raw, bytes):
            raw = raw.decode("ascii", "replace")
        if not raw.strip():
            return []
        return [int(part) for part in raw.split()]

    def fetch(self, mailbox: str, uids: list[int]) -> list[ImapMessage]:
        """BODY.PEEK[] in small UID batches. Does not set \\Seen."""
        if not uids:
            return []
        self._select(mailbox)
        uidvalidity = self.uidvalidity(mailbox)
        found: list[ImapMessage] = []
        client = self._conn()
        for start in range(0, len(uids), _UID_FETCH_BATCH):
            batch = uids[start : start + _UID_FETCH_BATCH]
            seq = ",".join(str(uid) for uid in batch)
            typ, data = client.uid("FETCH", seq, "(UID BODY.PEEK[])")
            if typ != "OK" or data is None:
                raise ImapMailboxError(f"FETCH {mailbox} failed: {typ}")
            for uid, raw in pairs_from_fetch(list(data)):
                found.append(
                    parse_rfc822(
                        raw,
                        mailbox=mailbox,
                        uid=uid,
                        uidvalidity=uidvalidity,
                    )
                )
        return found

    def close(self) -> None:
        """LOGOUT. Safe to call twice."""
        client = self._imap
        self._imap = None
        self._selected = None
        if client is None:
            return
        try:
            client.logout()
        except Exception:
            logger.debug("IMAP logout failed", exc_info=True)
