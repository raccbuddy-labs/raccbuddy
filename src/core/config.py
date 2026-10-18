"""Application configuration via environment variables."""

from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """RaccBuddy configuration loaded from environment.

    All values can be overridden via environment variables or a ``.env``
    file in the project root.  Names are case-insensitive.
    """

    # ------------------------------------------------------------------ #
    # Telegram
    # ------------------------------------------------------------------ #
    telegram_bot_token: str = ""
    owner_telegram_id: int = 0  # Set after first /start to lock the bot

    # ------------------------------------------------------------------ #
    # WhatsApp bridge
    # ------------------------------------------------------------------ #
    owner_whatsapp_number: str = ""  # Owner's WhatsApp number for recognition

    # ------------------------------------------------------------------ #
    # Database
    # ------------------------------------------------------------------ #
    database_url: str = (
        "postgresql+asyncpg://raccbuddy:raccbuddy@localhost:5432/raccbuddy"
    )
    # Async SQLAlchemy connection pool
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: int = 30

    # ------------------------------------------------------------------ #
    # LLM provider selection ("ollama", "xai", …)
    # ------------------------------------------------------------------ #
    llm_provider: str = "ollama"

    # Embedding provider selection ("ollama", "xai", …)
    # Default to ollama for embeddings regardless of main LLM provider
    embedding_provider: str = "ollama"

    # ------------------------------------------------------------------ #
    # Ollama
    # ------------------------------------------------------------------ #
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2:3b"
    ollama_embed_model: str = "nomic-embed-text"

    # ------------------------------------------------------------------ #
    # xAI (Grok)
    # ------------------------------------------------------------------ #
    xai_api_key: str = ""
    xai_model: str = "grok-4-1-fast-reasoning"
    xai_embed_model: str = (
        "v1"  # Replace with actual xAI embed model name (check xAI docs)
    )
    xai_embed_dimensions: int = 768
    xai_enable_builtin_tools: bool = False
    xai_temperature: float = 0.7
    xai_max_tokens: int = 8192
    xai_max_retries: int = 3  # Retry on transient gRPC errors (stale h2 connections)

    # ------------------------------------------------------------------ #
    # Context / Token limits
    # ------------------------------------------------------------------ #
    # How many tokens Ollama/xAI may use for the *full* context window.
    # Ollama receives this value as ``num_ctx`` so its KV-cache is sized
    # correctly.  Hard-limit for input = max_context_tokens - 1000
    # (1000 reserved for generation).
    max_context_tokens: int = 30_000

    # Embedding vector dimensions (must match the embed model output)
    embed_dimensions: int = 768

    # Max words per daily contact summary
    max_summary_words: int = 150

    # History catch-up. One LLM call per window of messages, one embed per
    # day, and a hard cap so a multi-year backfill cannot fill the GPU in a
    # single pass. The periodic summary job drains the rest.
    sync_summary_window: int = 40
    sync_summary_max_days: int = 10
    sync_summary_max_llm_calls: int = 30
    sync_summary_pause_seconds: float = 0.25
    # Re-read this many seconds before the cursor on incremental sync.
    sync_overlap_seconds: int = 120

    # ------------------------------------------------------------------ #
    # IMAP (optional pull connector)
    # ------------------------------------------------------------------ #
    imap_enabled: bool = False
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    imap_ssl: bool = True
    # From address treated as the owner. Defaults to the username.
    imap_owner_address: str = ""
    imap_mailboxes: str = "INBOX,Sent"
    # Bodies fetched per mailbox per run. Older mail continues next run.
    imap_history_max: int = 500
    imap_sync_interval_seconds: int = 300

    # ------------------------------------------------------------------ #
    # Matrix (optional pull connector, unencrypted rooms only)
    # ------------------------------------------------------------------ #
    matrix_enabled: bool = False
    matrix_homeserver: str = ""
    matrix_access_token: str = ""
    matrix_user_id: str = ""
    matrix_history_max_per_room: int = 500
    matrix_page_size: int = 50
    matrix_history_pause_seconds: float = 0.2
    matrix_sync_interval_seconds: int = 60

    # ------------------------------------------------------------------ #
    # Context builder tuning
    # ------------------------------------------------------------------ #
    # How many recent messages to include in every context window
    memory_recent_messages: int = 15
    # How many recent user↔bot conversation turns to include (each message = 1 turn)
    memory_conversation_turns: int = 10
    # How many semantic memory chunks to retrieve via pgvector
    memory_semantic_chunks: int = 6
    # How many past summaries to include
    memory_max_summaries: int = 3
    # Fraction of context budget allocated to owner self-memory
    memory_owner_budget_ratio: float = 0.20
    # Fraction of context budget allocated to contact semantic memories
    memory_contact_budget_ratio: float = 0.30
    # Minimum cosine similarity for owner memory inclusion
    memory_min_owner_relevance: float = 0.35

    # ------------------------------------------------------------------ #
    # REST API
    # ------------------------------------------------------------------ #
    api_port: int = 8000
    # Secret key for X-API-Key header on /api/messages.
    # Generate with: python -c "import secrets; print(secrets.token_hex(32))"
    # Leave empty to disable auth (not recommended for production).
    api_secret_key: str = ""

    # ------------------------------------------------------------------ #
    # Nudges
    # ------------------------------------------------------------------ #
    nudge_check_interval_minutes: int = 60

    # ------------------------------------------------------------------ #
    # Memory retention
    # ------------------------------------------------------------------ #
    owner_memory_retention_days: int = 90

    # ------------------------------------------------------------------ #
    # Sentiment / mood model (Ollama)
    # ------------------------------------------------------------------ #
    sentiment_model: str = "llama3.2:3b"

    # ------------------------------------------------------------------ #
    # Relationship scoring weights  (must sum to 1.0)
    # ------------------------------------------------------------------ #
    rel_weight_frequency: float = 0.30
    rel_weight_recency: float = 0.30
    rel_weight_sentiment: float = 0.25
    rel_weight_reply_rate: float = 0.15

    # ------------------------------------------------------------------ #
    # Tool calling
    # ------------------------------------------------------------------ #
    max_tool_rounds: int = 5

    # ------------------------------------------------------------------ #
    # Agentic proactive core (opt-in)
    # ------------------------------------------------------------------ #
    agentic_enabled: bool = False
    # Checkpointer backend for LangGraph state persistence ("postgres" | "sqlite")
    checkpointer_backend: str = "postgres"
    # Token budget for a single agentic cycle (separate from max_context_tokens)
    max_cycle_tokens: int = 8192
    # How often the agentic cycle runs (minutes)
    agentic_cycle_interval_minutes: int = 30

    # ------------------------------------------------------------------ #
    # Langfuse tracing (optional, self-hosted)
    # ------------------------------------------------------------------ #
    langfuse_enabled: bool = False
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://localhost:3000"

    # ------------------------------------------------------------------ #
    # Prometheus metrics (optional)
    # ------------------------------------------------------------------ #
    prometheus_enabled: bool = False
    prometheus_port: int = 9090

    # ------------------------------------------------------------------ #
    # Voice (STT + TTS)
    # ------------------------------------------------------------------ #
    # Master switch — voice handler is a no-op when False.
    voice_enabled: bool = False

    # Reply mode for voice messages: "text", "voice", or "both".
    voice_reply_mode: str = "text"

    # Optional ISO-639-1 language code to force for transcription
    # (empty = auto-detect).
    voice_language: str = ""

    # STT provider & model (see src/core/voice/providers/).
    stt_provider: str = "whisper"
    stt_model: str = "openai/whisper-small"

    # TTS provider & model.
    tts_provider: str = "bark"
    tts_model: str = "suno/bark-small"

    # Bark voice preset (e.g. "v2/en_speaker_6").
    tts_voice_preset: str = "v2/en_speaker_6"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
