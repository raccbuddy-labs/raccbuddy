"""Handler for regular chat messages and contact management."""

from __future__ import annotations

import datetime
import logging
import re

from telegram import MessageOriginUser, Update
from telegram.ext import ContextTypes

from src.core.auth import reject_non_owner
from src.core.config import settings
from src.core.db import (
    Contact,
    get_all_contacts_all_platforms,
    get_contact_by_name_any_platform,
    get_relationship,
    upsert_contact,
)
from src.core.llm import (
    SYSTEM_PROMPT,
    generate,
    generate_chat,
    generate_with_tools,
    provider_supports_tools,
)
from src.core.memory.context_builder import context_builder
from src.core.relationship import relationship_manager
from src.core.sentiment import mood_analyzer
from src.core.skills.base import get_registered_skills as get_registered_nudge_skills
from src.core.skills.chat import (
    collect_system_prompt_fragments,
    get_registered_chat_skills,
    run_post_processors,
    run_pre_processors,
)
from src.core.state import (
    flush_contact_state,
    flush_state,
    get_contact_state,
    get_state,
    update_contact_state,
)
from src.core.sync.facade import persist_incoming
from src.core.sync.keys import ensure_utc, fallback_external_message_id
from src.core.sync.types import IncomingRecord
from src.core.tools import execute_tool, get_all_tool_schemas
from src.utils.telegram_format import md_to_telegram_html

logger = logging.getLogger(__name__)

# Pattern to detect name mapping in natural language
_NAME_PATTERN = re.compile(
    r"(?:this is|that'?s|call (?:them|her|him))\s+([A-Za-z]\w+)",
    re.IGNORECASE,
)


def _owner_id() -> int:
    """Return canonical owner ID (Telegram user ID from config)."""
    return settings.owner_telegram_id


def _message_timestamp(message: object) -> datetime.datetime:
    """Original Telegram send time, or now if the update has none."""
    raw = getattr(message, "date", None)
    if isinstance(raw, datetime.datetime):
        return ensure_utc(raw)
    return datetime.datetime.now(datetime.UTC)


def _telegram_external_id(message: object, chat_id: int, text: str) -> str:
    """Stable id for a Telegram message. Falls back to a content hash."""
    mid = getattr(message, "message_id", None)
    if isinstance(mid, int):
        return str(mid)
    return fallback_external_message_id(
        platform="telegram",
        external_chat_id=str(chat_id),
        timestamp=_message_timestamp(message),
        sender_handle=str(getattr(getattr(message, "from_user", None), "id", "")),
        text=text,
    )


def _telegram_record(
    message: object,
    *,
    chat_id: int,
    text: str,
    external_message_id: str,
    is_from_owner: bool,
    is_bot_reply: bool = False,
    contact_id: int | None = None,
    sender_handle: str = "",
    sender_name: str | None = None,
    edited_at: datetime.datetime | None = None,
) -> IncomingRecord:
    """Build an ingest record for one Telegram update."""
    return IncomingRecord(
        platform="telegram",
        external_chat_id=str(chat_id),
        external_message_id=external_message_id,
        text=text,
        timestamp=_message_timestamp(message),
        sender_handle=sender_handle,
        sender_name=sender_name,
        is_from_owner=is_from_owner,
        is_bot_reply=is_bot_reply,
        contact_id=contact_id,
        edited_at=edited_at,
    )


async def chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process an incoming text message: save, build context, reply."""
    if not update.message or not update.message.text or not update.effective_user:
        return

    if await reject_non_owner(update):
        return

    user_id = update.effective_user.id
    owner = _owner_id() or user_id
    chat_id = update.effective_chat.id if update.effective_chat else user_id
    text = update.message.text

    # Check for forwarded message → extract original sender as contact
    forwarded_user_id = _extract_contact_id(update)

    # Resolve contact only for forwarded messages; owner direct messages
    # are *not* contact-scoped.
    contact: Contact | None = None
    if forwarded_user_id:
        contact_handle = str(forwarded_user_id)
        contact = await upsert_contact(
            owner_id=owner,
            contact_handle=contact_handle,
            platform="telegram",
            contact_name=update.effective_user.first_name or contact_handle,
        )

        # Store last forwarded contact for /name command
        state = get_state(user_id)
        state.extra["last_forwarded_contact_db_id"] = contact.id

        # Update per-contact state
        cs = get_contact_state(owner, contact.id)
        cs.last_message_at = datetime.datetime.now(datetime.UTC)
        cs.message_count += 1

    # Check for name mapping pattern (e.g., "this is Giulia")
    name_match = _NAME_PATTERN.search(text)
    last_fwd_db_id = get_state(user_id).extra.get("last_forwarded_contact_db_id")
    if name_match and last_fwd_db_id:
        name = name_match.group(1)
        await _map_contact_name_by_id(last_fwd_db_id, name)
        get_state(user_id).extra.pop("last_forwarded_contact_db_id", None)
        await update.message.reply_text(
            f"Got it! I'll remember this contact as {name} 🦝"
        )
        return

    # Persist by Telegram message id. A redelivered update (getUpdates
    # retry, process restart before the offset was acked) is a duplicate
    # and does not generate a second reply.
    external_id = _telegram_external_id(update.message, chat_id, text)
    sender_handle = str(forwarded_user_id or user_id)
    ingested = await persist_incoming(
        [
            _telegram_record(
                update.message,
                chat_id=chat_id,
                text=text,
                external_message_id=external_id,
                is_from_owner=contact is None,
                contact_id=contact.id if contact else None,
                sender_handle=sender_handle,
                sender_name=update.effective_user.first_name,
            )
        ],
        owner_id=owner,
    )
    if ingested.is_replay is True:
        logger.info("Ignoring redelivered Telegram message %s", external_id)
        return

    # Update in-memory user state
    user_state = get_state(user_id)
    user_state.last_active = datetime.datetime.now(datetime.UTC)
    user_state.message_count_today += 1

    # --- Async enrichment (mood, relationship, state flush) ---
    contact_id = contact.id if contact else None
    await _enrich_after_message(text, owner, contact_id)

    # Build context and generate a reply
    try:
        # Run chat-skill pre-processors
        text = await run_pre_processors(text, owner)

        system = _build_system_prompt()

        # Always use proper multi-turn messages for conversation coherence
        messages = await context_builder.build_messages(
            owner,
            contact_id,
            text,
            system,
        )

        if provider_supports_tools():
            reply = await _generate_with_tool_loop(messages, owner)
        else:
            reply = await generate_chat(messages)

        # Run chat-skill post-processors
        reply = await run_post_processors(reply, owner)

        await update.message.reply_text(md_to_telegram_html(reply), parse_mode="HTML")

        # Persist bot reply for conversation history. Keyed off the user
        # message id so a retry does not store a second assistant turn.
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
    except Exception:
        logger.exception("Failed to generate reply for user %d", user_id)
        await update.message.reply_text(
            "Oops, my raccoon brain glitched 🦝 Try again in a sec!"
        )


async def edited_message_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Apply a Telegram edit to the existing row. Does not send a new reply."""
    message = update.edited_message
    if not message or not message.text or not update.effective_user:
        return
    if await reject_non_owner(update):
        return

    user_id = update.effective_user.id
    owner = _owner_id() or user_id
    chat_id = update.effective_chat.id if update.effective_chat else user_id
    edited_at = (
        message.edit_date if isinstance(message.edit_date, datetime.datetime) else None
    )
    if edited_at is not None:
        edited_at = ensure_utc(edited_at)
    external_id = _telegram_external_id(message, chat_id, message.text)
    await persist_incoming(
        [
            _telegram_record(
                message,
                chat_id=chat_id,
                text=message.text,
                external_message_id=external_id,
                is_from_owner=True,
                sender_handle=str(user_id),
                edited_at=edited_at or datetime.datetime.now(datetime.UTC),
            )
        ],
        owner_id=owner,
    )


async def _enrich_after_message(
    text: str,
    owner_id: int,
    contact_id: int | None,
) -> None:
    """Run lightweight enrichment after a message is saved.

    - Mood / sentiment detection → updates user & contact state.
    - Relationship score recalculation (contact messages only).
    - Flush dirty state to DB.

    All steps are wrapped in individual try/except so a failure in one
    does not block the others.
    """
    # 1. Mood detection
    try:
        mood, valence = await mood_analyzer.detect_and_store(text, owner_id, contact_id)

        # Update user-level mood
        from src.core.state import update_state

        update_state(owner_id, mood=mood)

        # Update contact-level mood
        if contact_id is not None:
            update_contact_state(owner_id, contact_id, mood=mood)
    except Exception:
        logger.warning("Mood enrichment failed", exc_info=True)

    # 2. Relationship score recalculation (only for contact messages)
    if contact_id is not None:
        try:
            score = await relationship_manager.calculate_score(contact_id, owner_id)
            update_contact_state(owner_id, contact_id, score=score)
        except Exception:
            logger.warning("Relationship scoring failed", exc_info=True)

    # 3. Flush dirty state to DB
    try:
        await flush_state(owner_id)
        if contact_id is not None:
            await flush_contact_state(owner_id, contact_id)
    except Exception:
        logger.warning("State flush failed", exc_info=True)


def _extract_contact_id(update: Update) -> int | None:
    """Extract the original sender ID from a forwarded message."""
    msg = update.message
    if msg and msg.forward_origin and isinstance(msg.forward_origin, MessageOriginUser):
        return msg.forward_origin.sender_user.id
    return None


async def _map_contact_name(
    owner_id: int,
    contact_handle: str,
    name: str,
    platform: str = "telegram",
) -> None:
    """Save or update a contact name mapping by contact handle."""
    await upsert_contact(
        owner_id=owner_id,
        contact_handle=contact_handle,
        platform=platform,
        contact_name=name,
    )


async def _map_contact_name_by_id(
    contact_db_id: int,
    name: str,
) -> None:
    """Save or update a contact name mapping by database ID."""
    from src.core.db import get_contact_by_id

    contact = await get_contact_by_id(contact_db_id)
    if contact:
        await upsert_contact(
            owner_id=contact.owner_id,
            contact_handle=contact.contact_handle,
            platform=contact.platform,
            contact_name=name,
        )


async def _resolve_contact_by_name(
    owner_id: int,
    name: str,
) -> Contact | None:
    """Look up a contact by name across all platforms."""
    return await get_contact_by_name_any_platform(owner_id, name)


async def name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /name command to map a forwarded contact to a name."""
    if not update.message or not update.effective_user:
        return

    if await reject_non_owner(update):
        return

    user_id = update.effective_user.id
    args = context.args

    if not args:
        await update.message.reply_text(
            "Usage: Forward a message, then reply with /name <Name> 🦝"
        )
        return

    name = " ".join(args)
    state = get_state(user_id)
    last_contact_db_id = state.extra.get("last_forwarded_contact_db_id")

    if not last_contact_db_id:
        await update.message.reply_text(
            "Forward me a message first, then use /name <Name> 🦝"
        )
        return

    await _map_contact_name_by_id(last_contact_db_id, name)
    state.extra.pop("last_forwarded_contact_db_id", None)
    await update.message.reply_text(f"Got it! I'll remember this contact as {name} 🦝")


async def analyze_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /analyze <name> — relationship analysis for a contact."""
    if not update.message or not update.effective_user:
        return

    if await reject_non_owner(update):
        return

    user_id = update.effective_user.id
    owner = _owner_id() or user_id
    args = context.args

    if not args:
        await update.message.reply_text("Usage: /analyze <contact name> 🦝")
        return

    name = " ".join(args)
    contact = await _resolve_contact_by_name(owner, name)
    if not contact:
        await update.message.reply_text(
            f"I don't know anyone called {name}. " "Forward me their messages first! 🦝"
        )
        return

    try:
        ctx = await context_builder.build(
            owner,
            contact.id,
            f"Analyze my relationship with {name}",
        )
        prompt = (
            f"{ctx}\n\n"
            f"Brief relationship analysis for '{name}': communication "
            f"patterns, emotional tone, suggestions. Max 150 words."
        )
        reply = await generate(prompt)
        await update.message.reply_text(
            md_to_telegram_html(f"📊 Analysis for {name}:\n\n{reply}"),
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("Failed to analyze contact %s", name)
        await update.message.reply_text(
            "Something went wrong analyzing that contact 🦝"
        )


async def insights_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /insights <name> — conversation insights for a contact."""
    if not update.message or not update.effective_user:
        return

    if await reject_non_owner(update):
        return

    user_id = update.effective_user.id
    owner = _owner_id() or user_id
    args = context.args

    if not args:
        await update.message.reply_text("Usage: /insights <contact name> 🦝")
        return

    name = " ".join(args)
    contact = await _resolve_contact_by_name(owner, name)
    if not contact:
        await update.message.reply_text(
            f"I don't know anyone called {name}. " "Forward me their messages first! 🦝"
        )
        return

    try:
        ctx = await context_builder.build(
            owner,
            contact.id,
            f"Insights about {name}",
        )
        prompt = (
            f"{ctx}\n\n"
            f"Concise insights about chat with '{name}': key topics, "
            f"sentiment shifts, notable patterns. Max 150 words."
        )
        reply = await generate(prompt)
        await update.message.reply_text(
            md_to_telegram_html(f"💡 Insights for {name}:\n\n{reply}"),
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("Failed to get insights for contact %s", name)
        await update.message.reply_text("Something went wrong getting insights 🦝")


async def relationship_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /relationship <name> — show relationship score."""
    if not update.message or not update.effective_user:
        return

    if await reject_non_owner(update):
        return

    user_id = update.effective_user.id
    owner = _owner_id() or user_id
    args = context.args

    if not args:
        await update.message.reply_text("Usage: /relationship <contact name> 🦝")
        return

    name = " ".join(args)
    contact = await _resolve_contact_by_name(owner, name)
    if not contact:
        await update.message.reply_text(
            f"I don't know anyone called {name}. " "Forward me their messages first! 🦝"
        )
        return

    rel = await get_relationship(contact.id)
    score = rel.score if rel else 50
    bar = "█" * (score // 10) + "░" * (10 - score // 10)
    await update.message.reply_text(
        f"🦝 Relationship with {name}: {score}/100\n[{bar}]"
    )


async def contacts_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle /contacts — list all known contacts across platforms."""
    if not update.message or not update.effective_user:
        return

    if await reject_non_owner(update):
        return

    user_id = update.effective_user.id
    owner = _owner_id() or user_id
    contacts = await get_all_contacts_all_platforms(owner)

    if not contacts:
        await update.message.reply_text(
            "No contacts yet! Forward me messages to get started 🦝"
        )
        return

    lines: list[str] = ["📋 Your contacts:\n"]
    for c in contacts:
        lines.append(f"• {c.contact_name} ({c.platform})")

    await update.message.reply_text("\n".join(lines))


# -------------------------------------------------------------------
# Tool-calling loop for advanced providers
# -------------------------------------------------------------------


async def _generate_with_tool_loop(
    messages: list[dict],
    owner_id: int,
) -> str:
    """Run a generate -> tool-call -> feed-result loop until the model
    produces a final text answer or the round limit is hit.

    Args:
        messages: Pre-built multi-turn messages list from context_builder.build_messages().
        owner_id: Telegram owner ID for tool execution.

    Returns:
        The final text reply from the model (tool diagnostics are logged, not returned).
    """
    all_tools = get_all_tool_schemas()

    for _round in range(settings.max_tool_rounds):
        result = await generate_with_tools(messages, all_tools)

        if result.finished and not result.tool_calls:
            return result.text or "🦝"

        if not result.tool_calls:
            return result.text or "🦝"

        # Log intermediate text (tool reasoning/diagnostics) — don't send to user
        if result.text:
            logger.info("Tool-loop round %d reasoning: %s", _round, result.text[:500])

        # Append the assistant message with tool calls
        assistant_msg: dict = {"role": "assistant", "content": result.text or ""}
        assistant_msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": _safe_json_dumps(tc.arguments),
                },
            }
            for tc in result.tool_calls
        ]
        messages.append(assistant_msg)

        # Execute each tool and feed results back
        for tc in result.tool_calls:
            logger.info(
                "LLM tool call: %s(%s)",
                tc.name,
                tc.arguments,
            )
            tool_result = await execute_tool(tc.name, tc.arguments, owner_id)
            logger.info("Tool result: %s", str(tool_result)[:500])
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_result,
                }
            )

    # Exhausted rounds — ask for a final answer without tools
    logger.warning(
        "Tool loop hit %d rounds, forcing final answer", settings.max_tool_rounds
    )
    result = await generate_with_tools(messages, [])
    return result.text or "🦝"


def _safe_json_dumps(obj: object) -> str:
    """JSON-serialize an object, falling back to str()."""
    import json

    try:
        return json.dumps(obj)
    except (TypeError, ValueError):
        return str(obj)


def _build_system_prompt() -> str:
    """Build the system prompt with chat-skill fragments appended."""
    today = datetime.date.today().strftime("%B %d, %Y")
    base_prompt = f"Today is {today}.\n\n{SYSTEM_PROMPT}"
    fragments = collect_system_prompt_fragments()
    if fragments:
        return f"{base_prompt}\n\n{fragments}"
    return base_prompt


# -------------------------------------------------------------------
# /skills command
# -------------------------------------------------------------------


async def skills_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle /skills — list all registered chat and nudge skills."""
    if not update.message or not update.effective_user:
        return

    if await reject_non_owner(update):
        return

    lines: list[str] = ["🦝 Registered skills:\n"]

    # Chat skills
    chat_skills = get_registered_chat_skills()
    if chat_skills:
        lines.append("💬 Chat skills:")
        for name, skill in chat_skills.items():
            lines.append(f"  • {name} — {skill.description}")
    else:
        lines.append("💬 No chat skills registered.")

    lines.append("")

    # Nudge skills
    nudge_skills = get_registered_nudge_skills()
    if nudge_skills:
        lines.append("🔔 Nudge skills:")
        for name, skill in nudge_skills.items():
            lines.append(f"  • {name} ({skill.trigger})")
    else:
        lines.append("🔔 No nudge skills registered.")

    lines.append("\nDrop .py files in skills/ or nudges/ to add more!")
    await update.message.reply_text("\n".join(lines))
