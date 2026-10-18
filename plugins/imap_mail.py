"""IMAP pull connector.

Auto-loaded from ``plugins/``. Does nothing unless ``IMAP_ENABLED=true``
and the host, username, and password are set. History goes through
:func:`src.connectors.imap_mail.sync_imap`, which dedupes on Message-ID.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.connectors.imap_mail import ImaplibTransport, sync_imap
from src.core.config import settings
from src.core.plugin_loader import register_plugin
from src.core.sync.sql_store import SqlMessageStore
from src.plugins.base import BasePlugin

logger = logging.getLogger(__name__)


def _mailboxes() -> list[str]:
    return [part.strip() for part in settings.imap_mailboxes.split(",") if part.strip()]


class ImapPlugin(BasePlugin):
    """Poll IMAP mailboxes and persist them with the shared history contract."""

    def __init__(self) -> None:
        self._stop = False
        self._task: asyncio.Task[None] | None = None

    @property
    def name(self) -> str:
        return "imap"

    @property
    def platform(self) -> str:
        return "imap"

    async def register(self, app: Any) -> None:
        """Start the poll loop when IMAP is configured. Otherwise return."""
        del app
        if not settings.imap_enabled:
            logger.info("IMAP connector disabled")
            return
        if not (
            settings.imap_host and settings.imap_username and settings.imap_password
        ):
            logger.warning("IMAP enabled but host, username, or password is empty")
            return
        if not settings.owner_telegram_id:
            logger.warning("IMAP enabled but OWNER_TELEGRAM_ID is not set")
            return
        self._stop = False
        self._task = asyncio.create_task(self._loop())

    async def handle_message(self, message: dict[str, Any]) -> None:
        """Unused. IMAP is pulled, not pushed."""
        del message

    async def sync_once(self) -> None:
        """One pass. Safe to call again."""
        owner = settings.imap_owner_address or settings.imap_username
        transport = ImaplibTransport(
            settings.imap_host,
            settings.imap_username,
            settings.imap_password,
            port=settings.imap_port,
            use_ssl=settings.imap_ssl,
        )
        try:
            store = SqlMessageStore(settings.owner_telegram_id)
            await sync_imap(
                transport,
                store,
                mailboxes=_mailboxes(),
                owner_address=owner,
                max_messages=settings.imap_history_max,
            )
        finally:
            transport.close()

    async def _loop(self) -> None:
        while not self._stop:
            try:
                await self.sync_once()
            except Exception:
                logger.exception("IMAP sync failed")
            try:
                await asyncio.sleep(settings.imap_sync_interval_seconds)
            except asyncio.CancelledError:
                return

    async def teardown(self) -> None:
        """Cancel the poll loop."""
        self._stop = True
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return


register_plugin(ImapPlugin())
