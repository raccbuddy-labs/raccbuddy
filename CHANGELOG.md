# Changelog

All notable changes to RaccBuddy will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Documentation

- Added `docs/analysis/2026-10-codebase-review.md`: architecture review, history-ingestion gap, and a reassessment of the small-model workarounds against a 16 GB RTX 5080.

### Fixed

- `ruff check` ignores the style rules added after the last green CI run (March 2026) so lint stays green without a drive-by rewrite of the tree.
- CI installs `numpy` so the Bark voice unit tests can build a fake audio buffer. The rest of the voice stack stays in `requirements-voice.txt`.

## [0.2.0-beta] - 2026-02-28

### Critical Fixes

- **Context window corrected** (`MAX_CONTEXT_TOKENS` default `4000` → `30000`). The previous default caused Ollama to silently truncate all long conversations.
- **Ollama `num_ctx` now passed on every request** — without this, Ollama allocated only a 2048-token KV-cache regardless of the Python-side setting, producing incoherent replies after short conversations. Fixed in `OllamaProvider.generate()`.
- **REST API authentication** — `POST /api/messages` now validates `X-API-Key` header against `API_SECRET_KEY` env var. Returns `401 Unauthorized` on mismatch. Auth disabled when key is empty (dev/LAN use).
- **Graceful shutdown** — PTB `post_shutdown` callback now flushes all dirty in-memory state to PostgreSQL and tears down plugins on SIGTERM, preventing up to several minutes of data loss per restart.
- **Docker cold-start race eliminated** — PostgreSQL `pg_isready` healthcheck added to `db` service; `app` and `whatsapp` services now use `condition: service_healthy`, preventing connection-refused crashes.

### ContextBuilder (New First-Class Module)

- **`src/core/memory/context_builder.py`** — New `ContextBuilder` class with layered, token-budgeted context assembly (per CLAUDE.md mandate):
  - Layer 1: Owner personal facts (semantic retrieval, configurable budget ratio)
  - Layer 2: User state snapshot (mood, message count, last active)
  - Layer 3: Contacts roster (for cross-contact awareness)
  - Layer 4: Contact semantic memories (pgvector hybrid search)
  - Layer 5: Daily summaries
  - Layer 6: Episodic recent messages
- All handlers (`chat_handler`, `analyze_handler`, `insights_handler`) and LLM tools now call `context_builder.build()` instead of `memory.get_relevant_context()`.
- Module-level `context_builder` singleton exported from `src/core/memory/__init__.py`.

### Token Logging

- **Ollama**: logs `prompt_tokens`, `output_tokens`, and `num_ctx` on every `generate()` call.
- **xAI**: logs `prompt_tokens`, `completion_tokens`, `total_tokens` on every `generate()` and `generate_with_tools()` call.

### Nudge Cooldown Persistence

- **New `NudgeCooldown` DB model** (`owner_id`, `skill_name`, `last_fired_at`, unique constraint `uq_owner_skill_cooldown`).
- Cooldown state is now written through to PostgreSQL on every fire via background asyncio task (PostgreSQL `ON CONFLICT DO UPDATE` upsert).
- `load_cooldowns_from_db()` called at startup to restore in-memory cooldown dict, surviving bot restarts.

### Bug Fixes

- **`detect_habits()` owner_id bug** — `get_all_habits()` previously returned habits for all users. Fixed: `get_all_habits(owner_id=user_id)` now filters by owner in `crud.py`, `nudges/engine.py`, and `skills/nudge.py`.
- **Ollama embed client** — `OllamaProvider.embed()` previously created a new `AsyncOpenAI` client on every call. Fixed: client is now lazily initialized and cached in `self._embed_client`.
- **Stale module-level constants** — `MAX_CONTEXT_CHARS` was computed once at import time from the wrong default. Replaced with dynamic `_context_budget_chars()` helper in `memory/base.py`.
- **`MAX_TOOL_ROUNDS` duplication** — removed local constant from `handlers/chat.py` and `providers/xai.py`; single source of truth is now `settings.max_tool_rounds`.

### Configuration Additions (`core/config.py`)

| New Setting | Default | Purpose |
|---|---|---|
| `embed_dimensions` | `768` | Single source of truth for embedding vector size |
| `db_pool_size` | `10` | SQLAlchemy connection pool size |
| `db_max_overflow` | `20` | Max overflow connections |
| `db_pool_timeout` | `30` | Pool checkout timeout (seconds) |
| `api_secret_key` | `""` | X-API-Key secret for REST API auth |
| `max_tool_rounds` | `10` | LLM tool-call loop limit |
| `memory_recent_messages` | `15` | Recent messages in context window |
| `memory_semantic_chunks` | `6` | pgvector chunks per query |
| `memory_max_summaries` | `3` | Past summaries in context |
| `memory_owner_budget_ratio` | `0.20` | Context budget fraction for owner facts |
| `memory_contact_budget_ratio` | `0.30` | Context budget fraction for contact memories |
| `memory_min_owner_relevance` | `0.35` | Min cosine similarity for owner memory inclusion |

### Infrastructure

- **DB connection pool tuning** — `pool_size`, `max_overflow`, `pool_timeout`, `pool_pre_ping=True` wired into `session.py` from config.
- **`EMBED_DIMENSIONS` single source of truth** — removed duplicate constants from `models.py` and `memory/base.py`; both now read `settings.embed_dimensions`.
- **`requirements.txt` upper bounds** — all dependencies now have explicit upper-bound version pins to prevent silent breaking upgrades.
- **`from __future__ import annotations`** — added to `api.py`, `start.py`, `config.py`, `context_builder.py`.
- **`/start` command** — removed phantom `/habits` and `/streak` commands that were listed in welcome text but never implemented; replaced with `/skills`.
- **`XAI_MODEL` default** corrected from `grok-4-1-fast-reasoning` → `grok-4-1-fast-reasoning` (matching xAI current offering).

---

## [0.1.0-beta] - 2026-02-22

### 🎉 Initial Beta Release

**RaccBuddy is ready for beta testing!** This release includes all core features for a privacy-first, local AI personal companion.

### Added

#### Core Features
- **Telegram bot integration** with natural conversation support
- **WhatsApp bridge** via Node.js service (whatsapp-web.js)
- **REST API** for external platform integrations (`POST /api/messages`)
- **PostgreSQL database** with pgvector for semantic search
- **Multi-LLM support**: Ollama (local) and xAI Grok (cloud with function calling)
- **Smart memory system** with embedding-based retrieval
- **Automatic conversation summarization** to keep context efficient
- **Owner memory deduplication** (cosine similarity > 0.9)

#### Relationship & Habits
- **Dynamic relationship scoring** with four weighted signals:
  - Message frequency (30%)
  - Recency (30%)
  - Sentiment (25%)
  - Reply rate (15%)
- **Mood/sentiment detection** on every message
- **Habit detection** combining frequency analysis and LLM pattern extraction
- **Contact management** across multiple platforms
- **Relationship score change history** tracking

#### Proactive Features
- **Nudge engine** with configurable check intervals
- **Built-in nudge skills**:
  - Idle detection (activity-based check-ins)
  - Contact quiet alerts (reminds to reach out)
  - Evening check-ins
  - Habit tracking reminders
- **Custom nudge skills** via extensible system (see `nudges/` folder)

#### LLM & AI
- **Function calling support** for advanced providers (xAI)
- **LLM Tools**:
  - `analyze_contact` - Run relationship analysis
  - `get_insights` - Get conversation insights
  - `get_relationship_score` - Retrieve score (0-100)
  - `list_contacts` - List all contacts
  - `summarize_contact` - Summarize recent history
  - `schedule_message` - Schedule future messages
- **Smart context management** via ContextBuilder (layered, token-budgeted, up to 30,000 tokens)
- **Semantic embedding search** with retrieval

#### Extensibility
- **Chat skills system** for customizing conversation behavior
- **Nudge skills system** for proactive reminders
- **Auto-discovery** of custom skills from `skills/` and `nudges/` folders
- **Plugin base class** for future platform extensions

#### Infrastructure
- **Docker Compose setup** with PostgreSQL + pgvector
- **Multi-stage Dockerfile** for optimized builds
- **Alembic migrations** for database schema management
- **DB-backed persistent state** (survives restarts)
- **Scheduled jobs system** with database persistence
- **Health check endpoint** (`GET /health`)
- **Comprehensive test suite** with pytest

#### Developer Experience
- **Full type hints** throughout codebase (Python 3.12+)
- **Async/await** for all I/O operations
- **Black + Ruff** code formatting
- **Comprehensive documentation** (README, inline docs)
- **Example skills** in `skills/` and `nudges/` folders
- **GitHub Copilot instructions** for contributors

### Configuration
- **Environment-based configuration** via `.env` file
- **Flexible LLM provider selection** (ollama, xai)
- **Configurable token limits** and performance tuning
- **Adjustable relationship scoring weights**
- **Memory retention policies** (90-day default for owner memories)

### Documentation
- Complete README with installation instructions
- Troubleshooting guide
- API reference
- Security & Privacy section
- FAQ
- Contributing guidelines
- Skills & extensibility docs

### Security & Privacy
- **Local-first architecture** (all data on your machine)
- **No cloud dependencies** (unless you choose xAI)
- **Proper .gitignore** for sensitive files
- **Non-root Docker user** for security
- **No hardcoded credentials**

---

## [Unreleased]

### Planned for Future Releases

#### Phase 6 (Voice Support)
- Voice message transcription
- Text-to-speech responses
- Audio file handling

#### Phase 7 (Multi-User)
- Family/team mode support
- Per-user state management
- Access control system

#### Phase 8 (More Platforms)
- Signal bridge
- Discord integration
- Matrix server support
- iMessage bridge (macOS)

#### Phase 9 (Web Dashboard)
- Web UI for insights
- Relationship visualization
- Habit tracking dashboard
- Configuration interface

#### Phase 10 (Community)
- Plugin marketplace
- Skill sharing platform
- Community nudge library

---

## Version History

- **0.2.0** (2026-02-27) - Professionalization pass: ContextBuilder, 30k context, API auth, graceful shutdown, cooldown persistence, Docker healthchecks
- **0.1.0-beta** (2026-02-22) - Initial beta release

---

**Note**: This is a beta release. While all core features are implemented and tested, expect occasional updates and improvements. Please report bugs and feedback via [GitHub Issues](https://github.com/dcbert/raccbuddy/issues).
