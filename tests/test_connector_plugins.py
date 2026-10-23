"""Pull-connector plugins stay idle until they are configured."""

from __future__ import annotations

import pytest

from src.core.config import settings
from src.core.plugin_loader import clear_plugins, get_registered_plugins


@pytest.fixture(autouse=True)
def _restore_flags():
    previous = {
        "imap_enabled": settings.imap_enabled,
        "imap_host": settings.imap_host,
        "imap_username": settings.imap_username,
        "imap_password": settings.imap_password,
        "matrix_enabled": settings.matrix_enabled,
        "matrix_homeserver": settings.matrix_homeserver,
        "matrix_access_token": settings.matrix_access_token,
        "matrix_user_id": settings.matrix_user_id,
        "owner_telegram_id": settings.owner_telegram_id,
    }
    yield
    for name, value in previous.items():
        setattr(settings, name, value)


@pytest.mark.asyncio
async def test_plugins_register_and_do_not_connect_when_disabled() -> None:
    clear_plugins()
    from plugins.imap_mail import ImapPlugin
    from plugins.matrix_chat import MatrixPlugin

    settings.imap_enabled = False
    settings.matrix_enabled = False
    imap = ImapPlugin()
    matrix = MatrixPlugin()
    await imap.register(None)
    await matrix.register(None)
    assert imap._task is None
    assert matrix._task is None
    names = set(get_registered_plugins())
    assert "imap" in names
    assert "matrix" in names


@pytest.mark.asyncio
async def test_enabled_without_credentials_does_not_start() -> None:
    from plugins.imap_mail import ImapPlugin
    from plugins.matrix_chat import MatrixPlugin

    settings.imap_enabled = True
    settings.imap_host = ""
    settings.imap_username = ""
    settings.imap_password = ""
    settings.owner_telegram_id = 1
    imap = ImapPlugin()
    await imap.register(None)
    assert imap._task is None

    settings.matrix_enabled = True
    settings.matrix_homeserver = "https://matrix.example"
    settings.matrix_access_token = ""
    settings.matrix_user_id = ""
    matrix = MatrixPlugin()
    await matrix.register(None)
    assert matrix._task is None
