# Connectors

RaccBuddy stores the owner's own conversations so a local model can remember
them. Telegram and WhatsApp are the connectors that shipped with the
project. IMAP and Matrix are optional pull connectors on the same ingest
path. Signal, Slack, Discord, and iMessage are not implemented; the notes
at the bottom say why, and there is no half-built code for them.

Umbrel is a deploy target (see the README), not a chat connector. This
repository has no Hermes integration.

## The pattern

There are two ways a connector reaches the database. Both end at the same
function.

### In-process

Telegram handlers in `src/handlers/chat.py` and `src/handlers/voice.py`
build an `IncomingRecord` and call `persist_incoming`. The bot process
owns the connection.

IMAP (`plugins/imap_mail.py`) and Matrix (`plugins/matrix_chat.py`) follow
that shape: a `BasePlugin` is auto-loaded from `plugins/`, `register(app)`
runs once at startup, and the plugin pulls history on a timer. The fetch
and parse code lives in `src/connectors/`. The plugin does not insert rows
itself.

### Out-of-process bridge

WhatsApp is a Node process (`whatsapp-service/`) that POSTs to
`POST /api/messages` and `POST /api/messages/batch`. The API maps the JSON
onto the same `IncomingRecord` and calls `persist_incoming`. Use this when
the provider's client is not a Python library (WhatsApp Web).

`plugins/*.py` files are imported at startup by `load_user_plugins()`
(`src/core/plugin_loader.py`). Each module calls `register_plugin()` at
import time. `BasePlugin` (`src/plugins/base.py`) requires:

| Member | Role |
| --- | --- |
| `name` | Registry key |
| `platform` | Stored on each message (`telegram`, `whatsapp`, `imap`, `matrix`) |
| `register(app)` | Called once from `post_init`. Start loops here. Return immediately when the connector is disabled. |
| `handle_message(dict)` | Push path. Pull connectors leave this as a no-op. |
| `teardown()` | Cancel tasks and close sockets. |

`plugins/example_echo.py` is a log-only sample. It used to call
`save_message` with arguments that function does not accept. Do not copy
that. `save_message` always inserts and has no provider id, so a retried
backfill would duplicate rows.

### History contract

Every connector that can see old messages must use the contract in
[sync.md](sync.md):

* Idempotency key: `(platform, external_chat_id, external_message_id)`.
* `timestamp` is when the message was sent.
* A second delivery of the same id and text is a duplicate. A different
  body is an edit and keeps the original timestamp.
* Cursors live in `sync_cursors`. Re-running a finished backfill inserts
  nothing.
* Do not call the LLM from the sync loop. The periodic summary job drains
  large backfills in windows (`SYNC_SUMMARY_WINDOW`, default 40) with a
  cap on days and generations per pass.
* Batch writes in chunks of at most 500, matching `POST /api/messages/batch`.

Config lives on `Settings` in `src/core/config.py` and `.env.example`.
Tests pass a fake transport into `sync_imap` / `sync_matrix`. They do not
open sockets.

## What is implemented

### IMAP

Enabled with `IMAP_ENABLED=true` plus host, username, and password.
`OWNER_TELEGRAM_ID` must be set, same as the REST API, because contact
rows are stored under that owner.

* Chat id is the mailbox name (`INBOX`, `Sent`, …).
* Message id is the `Message-ID` header, or `uid:{uidvalidity}:{uid}` when
  the header is missing.
* The UID watermark is a separate cursor, `uid:{mailbox}`, formatted
  `{uidvalidity}:{high}:{low}`. The message watermark cannot hold a UID
  because it stores the Message-ID.
* Each run fetches at most `IMAP_HISTORY_MAX` bodies (default 500). New
  UIDs above `high` come first. Leftover budget walks backward from `low`,
  so a large mailbox drains over later runs.
* `BODY.PEEK` is used so sync does not mark mail read.
* A UIDVALIDITY change drops the checkpoint and re-reads. Message-IDs
  still dedupe mail already stored.
* Messages whose From matches `IMAP_OWNER_ADDRESS` (default: the username)
  are `is_from_owner`. So are messages in a mailbox named like Sent
  (`Sent`, `Sent Items`, `Sent Mail`, `[Gmail]/Sent Mail`).
* Messages with no Date, or with no subject and no body, are skipped. The
  UID is still consumed.

Not available: mail the server has expunged. `UID SEARCH ALL` downloads
the UID list (not the bodies) for each mailbox on each run.

### Matrix

Enabled with `MATRIX_ENABLED=true`, `MATRIX_HOMESERVER`,
`MATRIX_ACCESS_TOKEN`, and `MATRIX_USER_ID` (the exact MXID). No password
login is implemented; create the token on the homeserver.

* `GET /_matrix/client/v3/sync?timeout=0` is the live cursor. `next_batch`
  is stored on scope `__sync__`.
* Older history is `GET /rooms/{roomId}/messages?dir=b`. The pagination
  token is stored on scope `backfill:{roomId}` so it is not overwritten by
  the event id.
* One run pulls at most `MATRIX_HISTORY_MAX_PER_ROOM` events (default 500)
  in pages of `MATRIX_PAGE_SIZE` (default 50), then waits
  `MATRIX_HISTORY_PAUSE_SECONDS` between pages. The next run continues
  from the saved token.
* Stored events are `m.room.message` with `m.text`, `m.notice`, or
  `m.emote`. `m.replace` updates the original event id and keeps
  `origin_server_ts`. Media-only messages are skipped.
* Rooms whose snapshot includes `m.room.encryption` or `m.room.encrypted`
  are skipped. There is no Olm/Megolm code.
* `is_group` is set on the in-memory record when the sync payload shows
  more than two joined members. The `messages` table has no group column,
  so the flag is not stored. Group traffic is still a contact thread, same
  as WhatsApp. Invites and left rooms are ignored.
* `is_from_owner` is true when `sender` equals `MATRIX_USER_ID`.

Not available: history the homeserver has already purged, and anything
inside an encrypted room.

## Ranked providers that are not implemented

These fit the product only in part, or they cannot do a real history
backfill from this Linux/Umbrel deploy. They are notes, not stubs.

1. **Signal.** High product fit (private 1:1 chats) and a poor history
   API. A linked `signal-cli` device receives messages from link time
   forward; the server does not hand the full archive to a new device.
   Shipping it would mean a second daemon and a connector that still
   cannot backfill. Not started.

2. **Slack.** `conversations.history` can page a workspace with stable
   timestamps and ts ids, so the ingest contract would fit. The product
   is the owner's personal chats, not a workspace bot, and Slack needs an
   installed app plus token scopes. Left as a bridge that could POST
   `/api/messages/batch` later. Not started.

3. **Discord.** A bot cannot read a person's DMs and servers unless it
   was invited. A user-account "self-bot" is against Discord's terms.
   Not started.

4. **iMessage / SMS.** The useful source is macOS `~/Library/Messages/chat.db`
   or a phone export. RaccBuddy's deploy target is Linux (Docker / Umbrel),
   where that database is not present. An export file could be posted to
   `/api/messages/batch` with the SMS id as `external_message_id` and the
   original date as `timestamp`. No importer is included.

## Tests

`tests/test_imap_sync.py`, `tests/test_matrix_sync.py`, and
`tests/test_connector_plugins.py` cover backfill paging, a second run
inserting nothing, send-time order, edits, UIDVALIDITY change, encrypted
rooms, and plugins that stay idle when disabled. The Matrix HTTP test uses
`httpx.MockTransport`. Nothing in these tests dials a server.
