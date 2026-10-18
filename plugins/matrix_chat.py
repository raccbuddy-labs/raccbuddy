"""Matrix pull connector.

Auto-loaded from ``plugins/``. Does nothing unless ``MATRIX_ENABLED=true``
and the homeserver, access token, and user id are set. Encrypted rooms
are skipped. History uses the same idempotent ingest as the other
connectors.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.connectors.matrix_chat import HttpxMatrixTransport, sync_matrix
from src.core.config import settings
from src.core.plugin_loader import register_plugin
from src.core.sync.sql_store import SqlMessageStore
from src.plugins.base import BasePlugin

logger = logging.getLogger(__name__)


class MatrixPlugin(BasePlugin):
    """Poll a Matrix homeserver and persist joined, unencrypted rooms."""

    def __init__(self) -> None:
        self._stop = False
        self._task: asyncio.Task[None] | None = None

    @property
    def name(self) -> str:
        return "matrix"

    @property
    def platform(self) -> str:
        return "matrix"

    async def register(self, app: Any) -> None:
        """Start the poll loop when Matrix is configured. Otherwise return."""
        del app
        if not settings.matrix_enabled:
            logger.info("Matrix connector disabled")
            return
        if not (
            settings.matrix_homeserver
            and settings.matrix_access_token
            and settings.matrix_user_id
        ):
            logger.warning("Matrix enabled but homeserver, token, or user id is empty")
            return
        if not settings.owner_telegram_id:
            logger.warning("Matrix enabled but OWNER_TELEGRAM_ID is not set")
            return
        self._stop = False
        self._task = asyncio.create_task(self._loop())

    async def handle_message(self, message: dict[str, Any]) -> None:
        """Unused. Matrix is pulled, not pushed."""
        del message

    async def sync_once(self) -> None:
        """One ``/sync`` plus any unfinished room backfill."""
        transport = HttpxMatrixTransport(
            settings.matrix_homeserver,
            settings.matrix_access_token,
        )
        try:
            store = SqlMessageStore(settings.owner_telegram_id)
            await sync_matrix(
                transport,
                store,
                owner_mxid=settings.matrix_user_id,
                max_per_room=settings.matrix_history_max_per_room,
                page_size=settings.matrix_page_size,
                pause_seconds=settings.matrix_history_pause_seconds,
            )
        finally:
            transport.close()

    async def _loop(self) -> None:
        while not self._stop:
            try:
                await self.sync_once()
            except Exception:
                logger.exception("Matrix sync failed")
            try:
                await asyncio.sleep(settings.matrix_sync_interval_seconds)
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


register_plugin(MatrixPlugin())
