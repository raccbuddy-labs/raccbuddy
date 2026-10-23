"""Optional pull connectors (IMAP, Matrix).

Telegram and WhatsApp do not live here. Telegram is an in-process bot.
WhatsApp is the Node bridge under ``whatsapp-service/``. Both persist
through :func:`src.core.sync.persist_incoming`, and so do the connectors
in this package. See ``docs/connectors.md``.
"""

from src.connectors.imap_mail import sync_imap
from src.connectors.matrix_chat import sync_matrix

__all__ = ["sync_imap", "sync_matrix"]
