"""Handler for Telegram voice messages.

Flow
----
1. Receive voice/audio message from Telegram.
2. Download the OGG file to a temp path.
3. Transcribe via :mod:`src.core.voice` (Whisper).
4. Send the transcription back to the user as confirmation.
5. Process the transcribed text through the standard chat pipeline
   (context building → LLM → reply).
6. Optionally synthesize the reply via TTS and send as a voice message.
7. Clean up temporary files.
"""

from __future__ import annotations

import datetime
import logging
import os
import tempfile
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from src.core.auth import reject_non_owner
from src.core.config import settings
from src.core.llm import SYSTEM_PROMPT, generate_chat, provider_supports_tools
from src.core.memory.context_builder import context_builder
from src.core.skills.chat import (
    collect_system_prompt_fragments,
    run_post_processors,
    run_pre_processors,
)
from src.core.state import get_state
from src.core.sync.facade import persist_incoming
from src.core.voice import voice_manager

logger = logging.getLogger(__name__)


def _owner_id() -> int:
    """Return canonical owner ID (Telegram user ID from config)."""
    return settings.owner_telegram_id


def _build_system_prompt() -> str:
    """Build the system prompt with chat-skill fragments appended."""
    fragments = collect_system_prompt_fragments()
    if fragments:
        return f"{SYSTEM_PROMPT}\n\n{fragments}"
    return SYSTEM_PROMPT


async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle an incoming voice or audio message.

    Downloads the audio, transcribes it, processes through the standard
    LLM pipeline, and replies with text and/or voice depending on config.
    """
    if not update.message or not update.effective_user:
        return

    if await reject_non_owner(update):
        return

    # Check if voice is enabled
    if not voice_manager.is_enabled:
        await update.message.reply_text(
            "Voice messages aren't enabled yet 🦝\n"
            "Set VOICE_ENABLED=true in your .env to use this feature!"
        )
        return

    user_id = update.effective_user.id
    owner = _owner_id() or user_id
    chat_id = update.effective_chat.id if update.effective_chat else user_id

    # Get the voice or audio attachment
    voice = update.message.voice or update.message.audio
    if not voice:
        return

    # Send a "processing" indicator
    processing_msg = await update.message.reply_text("🎤 Processing voice message…")

    tmp_audio_path: Path | None = None
    tts_path: Path | None = None

    try:
        # 1. Download the voice file from Telegram
        file = await voice.get_file()
        tmp_fd, tmp_name = tempfile.mkstemp(
            suffix=".oga",
            prefix="raccbuddy_voice_",
        )
        tmp_audio_path = Path(tmp_name)
        # Close the fd — python-telegram-bot will write the file
        os.close(tmp_fd)

        await file.download_to_drive(str(tmp_audio_path))
        logger.info(
            "Voice message downloaded: %s (%.1f KB)",
            tmp_audio_path,
            tmp_audio_path.stat().st_size / 1024,
        )

        # 2. Transcribe
        transcription = await voice_manager.transcribe(
            tmp_audio_path,
            language=settings.voice_language or None,
        )
        text = transcription.text

        if not text.strip():
            await processing_msg.edit_text(
                "I couldn't make out what you said 🦝 Try again?"
            )
            return

        # 3. Show the transcription
        await processing_msg.edit_text(f"🎤 *You said:* {text}", parse_mode="Markdown")

        # 4. Save the transcribed message under the Telegram message id.
        from src.handlers.chat import _telegram_external_id, _telegram_record

        external_id = _telegram_external_id(update.message, chat_id, text)
        ingested = await persist_incoming(
            [
                _telegram_record(
                    update.message,
                    chat_id=chat_id,
                    text=text,
                    external_message_id=external_id,
                    is_from_owner=True,
                    sender_handle=str(user_id),
                )
            ],
            owner_id=owner,
        )
        if ingested.is_replay is True:
            logger.info("Ignoring redelivered voice message %s", external_id)
            return

        # Update in-memory user state
        user_state = get_state(user_id)
        user_state.last_active = datetime.datetime.now(datetime.UTC)
        user_state.message_count_today += 1

        # 5. Enrich (mood, etc.) — import here to avoid circular deps
        from src.handlers.chat import _enrich_after_message

        await _enrich_after_message(text, owner, None)

        # 6. Build context and generate reply
        text = await run_pre_processors(text, owner)
        system = _build_system_prompt()

        if provider_supports_tools():
            from src.handlers.chat import _generate_with_tool_loop

            ctx = await context_builder.build(owner, None, text)
            reply = await _generate_with_tool_loop(ctx, text, owner)
        else:
            messages = await context_builder.build_messages(
                owner,
                None,
                text,
                system,
            )
            reply = await generate_chat(messages)

        reply = await run_post_processors(reply, owner)

        # 7. Save bot reply
        try:
            await persist_incoming(
                [
                    _telegram_record(
                        update.message,
                        chat_id=chat_id,
                        text=reply,
                        external_message_id=f"reply:{external_id}",
                        is_from_owner=True,
                        is_bot_reply=True,
                    )
                ],
                owner_id=owner,
            )
        except Exception:
            logger.warning("Failed to save bot reply", exc_info=True)

        # 8. Send reply based on voice_reply_mode
        reply_mode = settings.voice_reply_mode

        if reply_mode in ("text", "both"):
            from src.utils.telegram_format import md_to_telegram_html

            await update.message.reply_text(
                md_to_telegram_html(reply), parse_mode="HTML"
            )

        if reply_mode in ("voice", "both"):
            try:
                synthesis = await voice_manager.synthesize(
                    reply,
                    output_format="ogg",
                    voice_preset=settings.tts_voice_preset or None,
                )
                tts_path = synthesis.audio_path

                with open(tts_path, "rb") as audio_file:
                    await update.message.reply_voice(
                        voice=audio_file,
                        caption=reply if reply_mode == "voice" else None,
                    )
            except Exception:
                logger.warning(
                    "TTS synthesis failed, falling back to text", exc_info=True
                )
                if reply_mode == "voice":
                    # Only send text if we haven't already
                    await update.message.reply_text(reply)

    except ImportError as exc:
        logger.error("Voice dependencies missing: %s", exc)
        await processing_msg.edit_text(
            "Voice processing dependencies aren't installed 🦝\n"
            "Install with: pip install torch transformers scipy"
        )
    except RuntimeError as exc:
        logger.error("Voice processing error: %s", exc)
        await processing_msg.edit_text(f"Voice processing failed: {exc} 🦝")
    except Exception:
        logger.exception("Unexpected error in voice handler")
        await processing_msg.edit_text(
            "Oops, my raccoon ears glitched 🦝 Try again in a sec!"
        )
    finally:
        # Clean up temp files
        if tmp_audio_path and tmp_audio_path.exists():
            tmp_audio_path.unlink(missing_ok=True)
        if tts_path and tts_path.exists():
            tts_path.unlink(missing_ok=True)
