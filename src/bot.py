"""RaccBuddy Telegram bot entry point.

Runs the Telegram bot and the REST API (FastAPI) concurrently so that
external bridges (WhatsApp, etc.) can push messages into the core.

Lifecycle
---------
1. ``post_init``   — DB init, skill/plugin loading, job scheduling.
2. ``post_shutdown`` — Flush all dirty state to DB, teardown plugins.
"""

from __future__ import annotations

import logging
import threading

import uvicorn
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.api import api
from src.core.config import settings
from src.core.db import init_db
from src.core.memory import memory
from src.core.nudges import run_nudge_skills
from src.core.plugin_loader import (
    load_user_plugins,
    register_all_with_app,
    teardown_all_plugins,
)
from src.core.scheduled import restore_pending_jobs, set_app_reference
from src.core.skills.base import load_cooldowns_from_db
from src.core.skills.loader import load_all_user_skills
from src.core.state import flush_all_dirty, load_all_states
from src.handlers.chat import (
    analyze_handler,
    chat_handler,
    contacts_handler,
    edited_message_handler,
    insights_handler,
    name_handler,
    relationship_handler,
    skills_handler,
)
from src.handlers.jobs import cancel_handler, jobs_handler, schedule_handler
from src.handlers.start import start_handler
from src.handlers.voice import voice_handler
from src.summarizer import summarize_all_contacts

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Suppress INFO logs from Telegram library (keep only WARNING and above)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(
    logging.WARNING
)  # Telegram uses httpx for API calls


async def post_init(application: Application) -> None:
    """Initialize database and schedule background jobs after startup."""
    await init_db()
    await memory.setup()
    logger.info("Database initialized (memory system ready)")

    # Attach the database log handler so all WARNING+ records are persisted
    from src.core.db.log_handler import DatabaseLogHandler

    logging.getLogger().addHandler(DatabaseLogHandler())
    logger.info("Database log handler attached (WARNING+ → app_logs)")

    # Restore persistent state from DB
    await load_all_states()

    # Restore nudge cooldowns so nudges don't spam immediately after restart
    loaded_cooldowns = await load_cooldowns_from_db()
    if loaded_cooldowns:
        logger.info("Restored %d nudge cooldown(s) from DB", loaded_cooldowns)

    # Load user-provided nudge and chat skills from nudges/ and skills/
    loaded = load_all_user_skills()
    logger.info("Loaded %d user skill file(s)", loaded)

    # Load user-provided platform plugins from plugins/
    loaded_plugins = load_user_plugins()
    logger.info("Loaded %d user platform plugin(s)", loaded_plugins)

    # Register all plugins with the app
    await register_all_with_app(application)

    # Register app reference so LLM-scheduled jobs can use the job queue
    set_app_reference(application)

    # Restore any pending scheduled jobs from the database
    restored = await restore_pending_jobs()
    if restored:
        logger.info("Restored %d pending scheduled jobs", restored)

    # Schedule periodic nudge checks (skip when agentic handles it)
    if application.job_queue:
        if not settings.agentic_enabled:
            application.job_queue.run_repeating(
                nudge_job,
                interval=settings.nudge_check_interval_minutes * 60,
                first=60,
            )
        # Daily summarization job (every 6 hours)
        application.job_queue.run_repeating(
            summary_job,
            interval=6 * 3600,
            first=300,
        )
        # Periodic state flush (every 5 minutes)
        application.job_queue.run_repeating(
            state_flush_job,
            interval=300,
            first=120,
        )
        logger.info("Nudge, summary, and state-flush schedulers started")

    # Initialize agentic subsystem (opt-in via AGENTIC_ENABLED=true)
    if settings.agentic_enabled:
        from src.core.agentic import init_agentic

        await init_agentic()
        if application.job_queue:
            application.job_queue.run_repeating(
                agentic_job,
                interval=settings.agentic_cycle_interval_minutes * 60,
                first=120,
            )
            logger.info(
                "Agentic cycle scheduled (every %d min)",
                settings.agentic_cycle_interval_minutes,
            )


async def agentic_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Periodic job — run one agentic proactive cycle."""
    if context.bot:
        from src.core.agentic.engine import run_agentic_cycle

        await run_agentic_cycle(context.bot)


async def nudge_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Periodic job — evaluate all nudge skills once per cycle."""
    if context.bot:
        await run_nudge_skills(context.bot)


async def summary_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Periodic job: daily summaries, owner reflection, and pruning."""
    await summarize_all_contacts()

    owner_id = settings.owner_telegram_id
    if owner_id:
        await memory.consolidate_memories(owner_id)
        await memory.prune_old_memories(
            days=settings.owner_memory_retention_days,
        )

        # Run habit detection after summarisation
        try:
            from src.core.habits import habit_detector

            await habit_detector.run_full_detection(owner_id)
        except Exception:
            logger.warning("Habit detection failed", exc_info=True)


async def state_flush_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Periodic job: flush dirty in-memory state to the database."""
    try:
        await flush_all_dirty()
    except Exception:
        logger.warning("State flush job failed", exc_info=True)


async def post_shutdown(application: Application) -> None:
    """Flush all state and teardown plugins on graceful shutdown (SIGTERM)."""
    logger.info("RaccBuddy shutting down — flushing state and tearing down plugins…")
    try:
        await flush_all_dirty()
        logger.info("All dirty state flushed to DB")
    except Exception:
        logger.warning("State flush on shutdown failed", exc_info=True)

    if settings.agentic_enabled:
        try:
            from src.core.agentic import shutdown_agentic

            await shutdown_agentic()
            logger.info("Agentic subsystem torn down")
        except Exception:
            logger.warning("Agentic teardown on shutdown failed", exc_info=True)

    try:
        await teardown_all_plugins()
        logger.info("All plugins torn down")
    except Exception:
        logger.warning("Plugin teardown on shutdown failed", exc_info=True)

    logger.info("RaccBuddy shutdown complete 🦝")


def _start_api_server() -> None:
    """Run the FastAPI server in a background thread.

    This allows the Telegram bot and the REST API to coexist in the
    same process without blocking each other.
    """
    uvicorn.run(
        api,
        host="0.0.0.0",
        port=settings.api_port,
        log_level="info",
    )


def main() -> None:
    """Build and run the Telegram bot + REST API."""
    if not settings.telegram_bot_token:
        logger.error("TELEGRAM_BOT_TOKEN is not set — aborting.")
        return

    # Start the FastAPI REST API in a daemon thread
    api_thread = threading.Thread(target=_start_api_server, daemon=True)
    api_thread.start()
    logger.info("REST API started on port %d", settings.api_port)

    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Command handlers
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("name", name_handler))
    app.add_handler(CommandHandler("analyze", analyze_handler))
    app.add_handler(CommandHandler("insights", insights_handler))
    app.add_handler(CommandHandler("relationship", relationship_handler))
    app.add_handler(CommandHandler("contacts", contacts_handler))
    app.add_handler(CommandHandler("skills", skills_handler))
    app.add_handler(CommandHandler("jobs", jobs_handler))
    app.add_handler(CommandHandler("cancel", cancel_handler))
    app.add_handler(CommandHandler("schedule", schedule_handler))

    # Message handler for all non-command text (including forwarded)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_handler))
    # Edits update the stored row in place (same message id, new body).
    app.add_handler(
        MessageHandler(
            filters.UpdateType.EDITED_MESSAGE & filters.TEXT, edited_message_handler
        )
    )

    # Voice message handler (voice notes + audio files)
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, voice_handler))

    logger.info("RaccBuddy is starting... 🦝")
    app.run_polling()


if __name__ == "__main__":
    main()
