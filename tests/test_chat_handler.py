"""Tests for src.handlers.chat."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.handlers.chat import (
    analyze_handler,
    chat_handler,
    name_handler,
    relationship_handler,
)

# Patch auth to always allow in tests (owner check bypassed)
_AUTH_PATCH = patch(
    "src.handlers.chat.reject_non_owner", new_callable=AsyncMock, return_value=False
)
_OWNER_PATCH = patch("src.handlers.chat._owner_id", return_value=100)


@pytest.mark.asyncio
class TestChatHandler:
    """Validate chat message processing."""

    @_AUTH_PATCH
    @_OWNER_PATCH
    @patch("src.handlers.chat._enrich_after_message", new_callable=AsyncMock)
    @patch("src.handlers.chat.provider_supports_tools", return_value=False)
    @patch("src.handlers.chat.generate_chat", new_callable=AsyncMock)
    @patch("src.handlers.chat.context_builder.build_messages", new_callable=AsyncMock)
    @patch("src.handlers.chat.persist_incoming", new_callable=AsyncMock)
    async def test_processes_message_and_replies(
        self,
        mock_save: AsyncMock,
        mock_build: AsyncMock,
        mock_generate: AsyncMock,
        mock_supports_tools: MagicMock,
        mock_enrich: AsyncMock,
        mock_owner: MagicMock,
        mock_auth: AsyncMock,
    ) -> None:
        mock_build.return_value = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "Hello"},
        ]
        mock_generate.return_value = "Hey legend! 🦝"

        update = MagicMock()
        update.message.text = "Hello"
        update.message.forward_origin = None
        update.effective_user.id = 100
        update.effective_user.first_name = "TestUser"
        update.effective_chat.id = 200
        update.message.reply_text = AsyncMock()

        await chat_handler(update, MagicMock())

        # Owner direct message saved (first call), bot reply saved (second call)
        assert mock_save.await_count == 2
        user_record = mock_save.await_args_list[0].args[0][0]
        assert user_record.platform == "telegram"
        assert user_record.external_chat_id == "200"
        assert user_record.text == "Hello"
        assert user_record.is_bot_reply is False
        assert user_record.is_from_owner is True
        reply_record = mock_save.await_args_list[1].args[0][0]
        assert reply_record.text == "Hey legend! 🦝"
        assert reply_record.is_bot_reply is True
        # build_messages called with owner, contact_id=None, text, system prompt
        mock_build.assert_called_once()
        mock_generate.assert_called_once()
        update.message.reply_text.assert_called_once_with(
            "Hey legend! 🦝", parse_mode="HTML"
        )

    async def test_no_message_returns_early(self) -> None:
        update = MagicMock()
        update.message = None
        update.effective_user = MagicMock()

        await chat_handler(update, MagicMock())

    async def test_no_text_returns_early(self) -> None:
        update = MagicMock()
        update.message.text = None
        update.effective_user = MagicMock()

        await chat_handler(update, MagicMock())

    async def test_no_user_returns_early(self) -> None:
        update = MagicMock()
        update.message = MagicMock()
        update.message.text = "hello"
        update.effective_user = None

        await chat_handler(update, MagicMock())

    @_AUTH_PATCH
    @_OWNER_PATCH
    @patch("src.handlers.chat._enrich_after_message", new_callable=AsyncMock)
    @patch("src.handlers.chat.provider_supports_tools", return_value=False)
    @patch("src.handlers.chat.generate_chat", new_callable=AsyncMock)
    @patch("src.handlers.chat.context_builder.build_messages", new_callable=AsyncMock)
    @patch("src.handlers.chat.persist_incoming", new_callable=AsyncMock)
    async def test_llm_error_sends_fallback(
        self,
        mock_save: AsyncMock,
        mock_build: AsyncMock,
        mock_generate: AsyncMock,
        mock_supports_tools: MagicMock,
        mock_enrich: AsyncMock,
        mock_owner: MagicMock,
        mock_auth: AsyncMock,
    ) -> None:
        mock_build.side_effect = RuntimeError("LLM down")

        update = MagicMock()
        update.message.text = "Hello"
        update.message.forward_origin = None
        update.effective_user.id = 100
        update.effective_user.first_name = "TestUser"
        update.effective_chat.id = 200
        update.message.reply_text = AsyncMock()

        await chat_handler(update, MagicMock())

        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        assert "glitched" in text

    @_AUTH_PATCH
    @_OWNER_PATCH
    @patch("src.handlers.chat._enrich_after_message", new_callable=AsyncMock)
    @patch("src.handlers.chat.provider_supports_tools", return_value=False)
    @patch("src.handlers.chat.generate_chat", new_callable=AsyncMock)
    @patch("src.handlers.chat.context_builder.build_messages", new_callable=AsyncMock)
    @patch("src.handlers.chat.persist_incoming", new_callable=AsyncMock)
    @patch("src.handlers.chat.upsert_contact", new_callable=AsyncMock)
    async def test_forwarded_message_extracts_contact(
        self,
        mock_upsert: AsyncMock,
        mock_save: AsyncMock,
        mock_build: AsyncMock,
        mock_generate: AsyncMock,
        mock_supports_tools: MagicMock,
        mock_enrich: AsyncMock,
        mock_owner: MagicMock,
        mock_auth: AsyncMock,
    ) -> None:
        # Mock contact for forwarded user
        mock_contact = MagicMock()
        mock_contact.id = 999
        mock_upsert.return_value = mock_contact

        mock_build.return_value = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "Hey there"},
        ]
        mock_generate.return_value = "Noted!"

        from telegram import MessageOriginUser, User

        update = MagicMock()
        update.message.text = "Hey there"
        # Mock forward_origin properly
        forward_user = User(id=999, is_bot=False, first_name="Test")
        update.message.forward_origin = MessageOriginUser(
            sender_user=forward_user, date=None
        )
        update.effective_user.id = 100
        update.effective_user.first_name = "Owner"
        update.effective_chat.id = 100
        update.message.reply_text = AsyncMock()

        await chat_handler(update, MagicMock())

        # Should upsert with forwarded user's ID as handle
        mock_upsert.assert_called_once()
        assert mock_upsert.call_args[1]["contact_handle"] == "999"

        user_record = mock_save.await_args_list[0].args[0][0]
        assert user_record.text == "Hey there"
        assert user_record.contact_id == 999
        assert user_record.is_from_owner is False
        # build_messages called with the forwarded contact's DB ID
        mock_build.assert_called_once()

    @_AUTH_PATCH
    @_OWNER_PATCH
    @patch("src.handlers.chat._map_contact_name_by_id", new_callable=AsyncMock)
    async def test_name_mapping_pattern(
        self,
        mock_map: AsyncMock,
        mock_owner: MagicMock,
        mock_auth: AsyncMock,
    ) -> None:
        """'this is Giulia' maps last forwarded contact."""
        from src.core.state import get_state

        update = MagicMock()
        update.message.text = "this is Giulia"
        update.message.forward_origin = None
        update.effective_user.id = 100
        update.effective_user.first_name = "Owner"
        update.effective_chat.id = 100
        update.message.reply_text = AsyncMock()

        # Set up last forwarded contact in state
        state = get_state(100)
        state.extra["last_forwarded_contact_db_id"] = 999

        await chat_handler(update, MagicMock())

        mock_map.assert_called_once_with(999, "Giulia")
        update.message.reply_text.assert_called_once()
        assert "Giulia" in update.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
class TestNameHandler:
    """Validate /name command behaviour."""

    @_AUTH_PATCH
    @_OWNER_PATCH
    async def test_no_args_shows_usage(
        self, mock_owner: MagicMock, mock_auth: AsyncMock
    ) -> None:
        update = MagicMock()
        update.effective_user.id = 100
        update.message.reply_text = AsyncMock()

        ctx = MagicMock()
        ctx.args = []

        await name_handler(update, ctx)

        text = update.message.reply_text.call_args[0][0]
        assert "Usage" in text

    @_AUTH_PATCH
    @_OWNER_PATCH
    async def test_no_forwarded_contact(
        self, mock_owner: MagicMock, mock_auth: AsyncMock
    ) -> None:
        from src.core.state import get_state

        update = MagicMock()
        update.effective_user.id = 101
        update.message.reply_text = AsyncMock()

        ctx = MagicMock()
        ctx.args = ["Giulia"]

        state = get_state(101)
        state.extra.pop("last_forwarded_contact_db_id", None)

        await name_handler(update, ctx)

        text = update.message.reply_text.call_args[0][0]
        assert "Forward" in text


@pytest.mark.asyncio
class TestAnalyzeHandler:
    """Validate /analyze command."""

    @_AUTH_PATCH
    @_OWNER_PATCH
    async def test_no_args_shows_usage(
        self, mock_owner: MagicMock, mock_auth: AsyncMock
    ) -> None:
        update = MagicMock()
        update.effective_user.id = 100
        update.message.reply_text = AsyncMock()

        ctx = MagicMock()
        ctx.args = []

        await analyze_handler(update, ctx)

        text = update.message.reply_text.call_args[0][0]
        assert "Usage" in text

    @_AUTH_PATCH
    @_OWNER_PATCH
    @patch("src.handlers.chat._resolve_contact_by_name", new_callable=AsyncMock)
    async def test_unknown_contact(
        self, mock_resolve: AsyncMock, mock_owner: MagicMock, mock_auth: AsyncMock
    ) -> None:
        mock_resolve.return_value = None

        update = MagicMock()
        update.effective_user.id = 100
        update.message.reply_text = AsyncMock()

        ctx = MagicMock()
        ctx.args = ["Unknown"]

        await analyze_handler(update, ctx)

        text = update.message.reply_text.call_args[0][0]
        assert "don't know" in text


@pytest.mark.asyncio
class TestRelationshipHandler:
    """Validate /relationship command."""

    @_AUTH_PATCH
    @_OWNER_PATCH
    async def test_no_args_shows_usage(
        self, mock_owner: MagicMock, mock_auth: AsyncMock
    ) -> None:
        update = MagicMock()
        update.effective_user.id = 100
        update.message.reply_text = AsyncMock()

        ctx = MagicMock()
        ctx.args = []

        await relationship_handler(update, ctx)

        text = update.message.reply_text.call_args[0][0]
        assert "Usage" in text


@pytest.mark.asyncio
class TestSaveMessage:
    """Validate raw message persistence via db.save_message."""

    @patch("src.core.db.crud.get_session")
    async def test_saves_message_to_db(self, mock_get_session: MagicMock) -> None:
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.add = MagicMock()  # add() is sync in SQLAlchemy
        mock_get_session.return_value = mock_session

        from src.core.db import save_message

        await save_message(
            platform="telegram",
            chat_id=1,
            from_contact_id=2,
            text_content="test message",
        )

        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()

    @patch("src.core.db.crud.get_session")
    async def test_saves_message_with_contact_id(
        self,
        mock_get_session: MagicMock,
    ) -> None:
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.add = MagicMock()  # add() is sync in SQLAlchemy
        mock_get_session.return_value = mock_session

        from src.core.db import save_message

        await save_message(
            platform="telegram",
            chat_id=1,
            from_contact_id=999,
            text_content="forwarded message",
        )

        mock_session.add.assert_called_once()
        added_msg = mock_session.add.call_args[0][0]
        assert added_msg.from_contact_id == 999

    @patch("src.core.db.crud.get_session")
    async def test_saves_owner_message_without_contact(
        self,
        mock_get_session: MagicMock,
    ) -> None:
        """Owner direct messages should have from_contact_id=None."""
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.add = MagicMock()  # add() is sync in SQLAlchemy
        mock_get_session.return_value = mock_session

        from src.core.db import save_message

        await save_message(
            platform="telegram",
            chat_id=100,
            text_content="hey raccy",
        )

        mock_session.add.assert_called_once()
        added_msg = mock_session.add.call_args[0][0]
        assert added_msg.from_contact_id is None
