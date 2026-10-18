"""Message history sync: idempotent ingest, cursors, and backfill helpers.

Connectors (Telegram, WhatsApp, and later bridges) must persist through
:func:`persist_incoming` rather than inserting rows directly. Re-running a
backfill is safe: provider message ids are unique, order follows the
original timestamp, and edits update the existing row.
"""

from src.core.sync.facade import persist_incoming
from src.core.sync.types import IncomingRecord, IngestResult

__all__ = ["IncomingRecord", "IngestResult", "persist_incoming"]
