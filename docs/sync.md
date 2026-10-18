# Message history sync

RaccBuddy used to store only messages that arrived while a connector was
already running. There was no fetch of existing history and no cursor, so a
restart (or a first install) never saw the chats that were already on the
phone. Ingest is now idempotent. Re-running a backfill does not duplicate
rows.

## Contract

Every stored provider message has:

| Field | Meaning |
| --- | --- |
| `external_chat_id` | Provider chat id (WhatsApp JID, Telegram chat id, …) |
| `external_message_id` | Provider message id. Unique per `(platform, external_chat_id)` |
| `timestamp` | When the message was sent, not when it was ingested |
| `edited_at` | Set when a later delivery of the same id has a new body |
| `is_from_owner` | The owner sent it. It still belongs to the contact's thread |

The same id with the same text is a duplicate. The same id with a different
text updates the row and keeps the original `timestamp`. Reads order by
`(timestamp, external_message_id)`, so a late older message sorts correctly.

Cursors live in `sync_cursors`, one row per `(platform, scope)`. `scope` is
the provider chat id. `newest_at` moves forward only. Incremental sync
re-reads a short overlap (`SYNC_OVERLAP_SECONDS`, default 120) and depends
on the unique key. `backfill_complete` stays false while the connector
filled its whole page, so raising the page cap later can still reach older
messages.

`POST /api/messages` accepts the same JSON as before, plus optional
`external_message_id` and `edited_at`. Without an id, a SHA-256 of
platform, chat, timestamp, sender, and text is used, so retries of an
identical payload do not duplicate. A real edit needs the provider id.

`POST /api/messages/batch` takes `{ "messages": [...], "history_complete": false }`
(max 500). `GET /api/sync/cursor?platform=&scope=` returns the watermark.

Chat ids for non-Telegram platforms are a SHA-256 prefix, not Python's
`hash()`. Python's `hash()` changes every process, which split one WhatsApp
chat across many `chat_id` values. Telegram keeps the integer Telegram
already assigned, so existing Telegram rows still match.

## What each provider can actually backfill

### WhatsApp

`whatsapp-web.js` fires `message_create` only for messages that show up
while the bridge is connected. On ready, the bridge calls
`chat.fetchMessages({ limit })`.

Limits:

- That call returns the newest `limit` messages the linked WhatsApp Web
  session can see. It is not a full server archive. Very old messages stay
  missing until the phone has synced them into the Web client.
- Default cap is `WHATSAPP_HISTORY_MAX_PER_CHAT` (500). The recent page
  after a finished backfill is `WHATSAPP_HISTORY_PAGE_SIZE` (100).
- Set `WHATSAPP_HISTORY_ENABLED=false` to keep live-only behavior.
- Status broadcasts are skipped. Group chats are stored as sent; relationship
  scoring still treats the sender as a contact. Leave groups out of the
  linked account if you do not want them scored.
- The bridge pauses 750 ms between chats so a large account does not
  disconnect the Web session.

### Telegram

The Bot API has no method that returns prior chat history. Polling
(`getUpdates`) only delivers updates Telegram has not yet acknowledged,
roughly a day of undelivered updates, not the conversation. Forwarding a
message to the bot stores that one bubble.

History backfill is a Telegram Desktop export:

1. Desktop → Settings → Advanced → Export Telegram data → JSON.
2. `POST /api/import/telegram` with `{ "export": <contents of result.json>, "owner_from_id": "user123" }`.
   `owner_from_id` is the `from_id` of your own messages in that file.
3. Posting the same file again inserts nothing.

Live messages and edits (same `message_id`) go through the bot as before.
A redelivered update does not call the model a second time.

### Anything else posted to the API

Send `external_message_id` and the original `timestamp`. Use
`POST /api/messages/batch` for pages. Read the cursor before the next page.

## Summaries and embeddings after a backfill

The batch and import endpoints do not call the LLM. A year of history must
not become one sentiment call and one embed per message.

The existing 6-hour summary job also runs a catch-up:

- Messages are grouped by contact and UTC day.
- Each day is split into windows of `SYNC_SUMMARY_WINDOW` messages (40).
  One generation per window, then one embedding for the whole day.
- A pass stops after `SYNC_SUMMARY_MAX_DAYS` (10) or
  `SYNC_SUMMARY_MAX_LLM_CALLS` (30), whichever comes first. A day that
  would blow the call budget is left untouched for the next pass.
- `SYNC_SUMMARY_PAUSE_SECONDS` (0.25) waits between generations.

Mood detection still runs on live Telegram messages only. It does not run
on backfill.

## Migration

`alembic upgrade head` adds the new message columns, the partial unique
index `uq_messages_provider_key`, timestamp indexes, and `sync_cursors`.
Existing rows keep working: their provider ids are null and the unique
index ignores nulls. `is_from_owner` defaults to false, so old scores do
not change until new owner-side messages arrive.

WhatsApp chats ingested before this change used a per-process `hash()` as
`chat_id`. Those integers cannot be reconstructed. New WhatsApp messages
use the stable id, so a pre-upgrade WhatsApp thread and its post-upgrade
messages will not share `chat_id`. Telegram integer ids are unchanged.
There is no automatic rewrite of the old WhatsApp rows.

## IMAP and Matrix

Those connectors use this same contract. IMAP keys mail by `Message-ID`
and keeps the UID watermark on a separate cursor. Matrix keys events by
event id, stores `next_batch` on scope `__sync__`, and stores the
backward pagination token on `backfill:{roomId}`. Limits and the
providers that were considered and not built are in
[connectors.md](connectors.md).
