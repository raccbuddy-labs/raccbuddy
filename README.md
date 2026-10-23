<div align="center">
  <br>
  <img src="assets/img/logo.png" alt="RaccBuddy Logo" width="200">
  <h1>RaccBuddy 🦝</h1>
  <p><strong>Your private, local AI companion that helps you build better habits and stronger relationships</strong></p>
  <p>Running entirely on your machine — Zero cloud ☁️❌</p>

  <p>
    <img src="https://img.shields.io/badge/python-3.12+-blue.svg" alt="Python 3.12+">
    <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License: MIT">
    <img src="https://img.shields.io/badge/status-beta-yellow.svg" alt="Status: Beta">
    <img src="https://img.shields.io/badge/privacy-first-purple.svg" alt="Privacy First">
  </p>
</div>

---

## Table of Contents
- [Why RaccBuddy](#why-raccbuddy)
- [Features](#features)
- [Quick Start](#quick-start)
- [Usage](#usage)
- [Troubleshooting](#troubleshooting)
- [Deployment to Production](#deployment-to-production)
- [Platform Integrations](#platform-integrations)
- [Skills & Extensibility](#skills--extensibility)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [API Reference](#api-reference)
- [Configuration](#configuration)
- [FAQ](#faq)
- [Roadmap](#roadmap)
- [Security & Privacy](#security--privacy)
- [Getting Help & Community](#getting-help--community)
- [Contributing](#contributing)
- [License](#license)

---

## Why RaccBuddy

**Privacy First**: Everything runs locally on your machine — no cloud services, no data leaks, complete control over your personal data.

**Smart & Efficient**: Uses a layered `ContextBuilder` + pgvector semantic search to assemble up to 30,000 tokens of relevant context, delivering coherent, deeply personalised responses.

**Proactive Care**: Sends personalized nudges based on your habits and relationship patterns — helps you stay connected with those who matter.

**Multi-Platform**: Started with Telegram, now supports WhatsApp, and designed to integrate with more platforms via an extensible plugin system.

---

## Features

### 🤖 Intelligent Conversation
- **Smart Memory System**: Uses pgvector semantic search to retrieve relevant context efficiently
- **Multi-LLM Support**: Works with local Ollama models (llama3.2, qwen2.5) or cloud providers (xAI Grok)
- **Function Calling**: Advanced LLM providers can call tools autonomously (analyze contacts, get insights, schedule messages)
- **ContextBuilder**: A layered, token-budgeted context assembly pipeline (30,000 token default, fully configurable)
- **Multi-turn Conversations**: Proper user/assistant alternating message history for coherent dialogue (via `generate_chat()` + Ollama `/api/chat`)

### 💬 Platform Support
- **Telegram Bot**: Full-featured bot with inline commands and natural conversation
- **WhatsApp Integration**: Node.js bridge service connects WhatsApp messages to the same AI pipeline
- **REST API**: `/api/messages` endpoint for adding new platform bridges (Signal, Discord, etc.)

### 🎯 Proactive Nudges
Built-in nudge skills that trigger based on real data:
- **Idle Detection**: Checks in when you've been inactive after recent activity
- **Contact Quiet**: Reminds you to reach out when a regular contact goes silent
- **Evening Check-in**: End-of-day reflections and gentle reminders
- **Habit Tracking**: Monitors your custom habits and sends motivational nudges

### 🛠️ LLM Tool Calling
When using advanced providers (xAI, OpenAI), Raccy can autonomously:
- Analyze contact relationships and communication patterns
- Retrieve relationship scores (0-100)
- Get conversation insights and sentiment analysis
- List all contacts across platforms
- Summarize historical conversations
- Schedule future messages

### 🎤 Voice Messages
- **Local transcription** via OpenAI Whisper (configurable model size)
- **Text-to-speech** via Suno Bark for voice replies
- **Audio format conversion** handled transparently via ffmpeg
- **Reply mode**: text-only, voice-only, or both (configurable)
- Fully opt-in: set `VOICE_ENABLED=true` to activate

### 🧠 Agentic Proactive Core (Opt-in)
- **LangGraph-based** 4-node supervisor graph: ContextKeeper -> NudgePlanner -> Crafter -> Reflector
- **Quality-gated nudges**: the Reflector LLM evaluates each crafted nudge before delivery
- **Checkpointed state**: survives restarts via PostgreSQL or SQLite backend
- **Observability**: optional Langfuse tracing and Prometheus metrics
- Fully opt-in: set `AGENTIC_ENABLED=true` to activate

### 🔌 Extensible Skills System

**Chat Skills**: Customize conversation behavior
- Inject system prompt fragments
- Expose custom tools to the LLM
- Pre/post-process messages
- See `skills/` folder for examples

**Nudge Skills**: Create custom proactive reminders
- Pure data checks (no LLM unless triggered)
- Cooldown periods to avoid spam
- Dynamic context injection
- See `nudges/` folder for examples

### 📊 Relationship Tracking
- Automatic contact discovery across platforms
- **Dynamic relationship scoring** using four weighted signals: message frequency, recency, sentiment, and reply rate
- **Mood / sentiment detection** on every message via lightweight LLM classification
- **Habit detection** combining frequency analysis and LLM pattern extraction
- Conversation summarization with semantic search
- Platform-agnostic contact management
- **Score-change event history** for tracking relationship trends over time

---

## Quick Start

> **🚀 Beta Release**: RaccBuddy is in active beta. Features are stable but expect occasional updates and improvements.

### Prerequisites
- **Python 3.12+** (for local installation) or **Docker** (for containerized setup)
- **Telegram account** for bot interaction
- **(Recommended)** [Ollama](https://ollama.ai) installed locally for LLM processing
- **(Alternative)** xAI API key for cloud-based LLM (with function calling support)

---

### Installation Method 1: Docker (Recommended)

**Easiest way to get started. Everything runs in containers.**

#### 1. Clone the Repository

```bash
git clone https://github.com/dcbert/raccbuddy.git
cd raccbuddy
```

#### 2. Get Your Telegram Bot Token

1. Open Telegram and message [@BotFather](https://t.me/botfather)
2. Send `/newbot` and follow the prompts
3. Copy the bot token (looks like `1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`)

#### 3. Configure Environment

```bash
cp .env.example .env
nano .env  # or use your preferred editor
```

**Minimal configuration** (edit these in `.env`):

```dotenv
# Required: Your Telegram bot token from BotFather
TELEGRAM_BOT_TOKEN=your-bot-token-here

# LLM Provider: 'ollama' (local, private) or 'xai' (cloud, with function calling)
LLM_PROVIDER=ollama

# If using Ollama on your host machine (recommended):
OLLAMA_BASE_URL=http://host.docker.internal:11434
```

#### 4. Install Ollama (if using local LLM)

```bash
# macOS / Linux: Download from https://ollama.ai
# Then pull the models:
ollama pull llama3.2:3b
ollama pull nomic-embed-text
```

#### 5. Start RaccBuddy

```bash
docker compose up -d
```

This starts:
- PostgreSQL database with pgvector
- RaccBuddy bot and API server
- (Optional) WhatsApp bridge

#### 6. Talk to Your Bot

1. Open Telegram and find your bot (@your_bot_username)
2. Send `/start` — Raccy will introduce themselves! 🦝
3. Start chatting naturally

**Verify it's running:**

```bash
# Check container logs
docker compose logs -f app

# Health check
curl http://localhost:8000/health
```

---

### Installation Method 2: Local Python (Advanced)

**For development or custom setups.**

#### 1. Clone and Setup

```bash
git clone https://github.com/dcbert/raccbuddy.git
cd raccbuddy
python3.12 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

#### 2. Configure Environment

```bash
cp .env.example .env
nano .env  # Edit with your settings
```

**Required settings for local setup:**

```dotenv
TELEGRAM_BOT_TOKEN=your-bot-token-here
DATABASE_URL=postgresql+asyncpg://raccbuddy:raccbuddy@localhost:5432/raccbuddy
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2:3b
OLLAMA_EMBED_MODEL=nomic-embed-text
```

#### 3. Start Database

```bash
docker compose up -d db
```

#### 4. Install and Configure Ollama

```bash
# Download from https://ollama.ai
ollama pull llama3.2:3b
ollama pull nomic-embed-text
```

#### 5. Run Database Migrations

```bash
alembic upgrade head
```

#### 6. Start the Bot

```bash
python -m src.bot
```

**Optional: Start the REST API (for WhatsApp/extensions)**

```bash
# In a separate terminal:
uvicorn src.api:api --host 0.0.0.0 --port 8000
```

---

### Verification & Testing

```bash
# Run the test suite
pytest tests/ -v

# Check database connection
docker exec -it $(docker ps -qf "name=raccbuddy-db") psql -U raccbuddy -d raccbuddy -c "\dt"

# View logs (Docker)
docker compose logs -f app

# API health check
curl http://localhost:8000/health
```

---

## Usage

### Basic Commands

Once your bot is running, message it on Telegram:

- `/start` — Initialize your account and meet Raccy
- `/help` — Show available commands (if implemented)
- Just chat naturally! — Raccy remembers context and learns about you

### First Time Setup

1. **Find your bot** on Telegram using the username you created with BotFather
2. **Send `/start`** — This registers you as the owner and locks the bot to your account
3. **Start chatting** — Ask questions, share updates, or just talk about your day
4. **Raccy will learn** — As you chat, Raccy builds a memory of your relationships and habits

### Stopping the Service

**Docker:**
```bash
docker compose down  # Stop all services
docker compose down -v  # Stop and remove data (warning: deletes database)
```

**Local Python:**
```bash
# Press Ctrl+C in the terminal running the bot
```

### Viewing Logs

**Docker:**
```bash
docker compose logs -f app  # Follow bot logs
docker compose logs -f db   # Follow database logs
```

**Local:**
```bash
# Logs appear in the terminal where you ran `python -m src.bot`
```

---

## Troubleshooting

### Bot doesn't respond

**Check if the bot is running:**
```bash
# Docker
docker compose ps

# Local
# Look for the python process in your terminal
```

**Check logs for errors:**
```bash
# Docker
docker compose logs app

# Local - errors appear in terminal
```

**Verify your bot token:**
```bash
# Test the token directly
curl https://api.telegram.org/bot<YOUR_TOKEN>/getMe
```

### Database connection errors

**Ensure PostgreSQL is running:**
```bash
docker compose ps db
docker compose logs db
```

**Check if the port is available:**
```bash
lsof -i :5432  # Should show postgres if running
```

**Restart the database:**
```bash
docker compose restart db
```

### Ollama connection errors

**Check if Ollama is running:**
```bash
curl http://localhost:11434/api/tags
```

**Verify models are pulled:**
```bash
ollama list
# Should show llama3.2:3b and nomic-embed-text
```

**Docker can't reach host Ollama:**
- Make sure you're using `http://host.docker.internal:11434` in docker-compose.yml
- This is already configured correctly in the default setup

### "Module not found" errors

**Ensure dependencies are installed:**
```bash
# Docker - rebuild the image
docker compose build app

# Local
source venv/bin/activate
pip install -r requirements.txt
```

### Database migration errors

**Reset and rerun migrations:**
```bash
# Warning: This deletes all data
docker compose down -v
docker compose up -d db
alembic upgrade head
```

### WhatsApp bridge issues

**Check if the WhatsApp service is running:**
```bash
docker compose logs whatsapp
```

**Rescan QR code:**
```bash
docker compose down whatsapp
docker volume rm raccbuddy_wa-session
docker compose up whatsapp
# Scan the new QR code
```

---

## Deployment to Production

### Deploying to Umbrel or Remote Servers

RaccBuddy includes automated scripts for deploying to production environments like Umbrel home servers.

#### Step 1: Build and Push to Docker Hub

On your development machine (Apple Silicon or any platform):

```bash
# Set your Docker Hub username
export DOCKER_USERNAME=your-dockerhub-username

# Run the deployment script
./scripts/deploy-to-dockerhub.sh
```

This script will:
- Build multi-platform Docker images (linux/amd64, linux/arm64)
- Push images to Docker Hub
- Dump your PostgreSQL database
- Backup your WhatsApp session (to avoid re-scanning QR)
- Create a ready-to-use `docker-compose.yml` with your image references
- Save everything to `./backups/YYYYMMDD_HHMMSS/`

#### Step 2: Deploy on Umbrel

1. **Copy the backup directory** to your Umbrel:
   ```bash
   scp -r backups/20260223_120000/ umbrel@umbrel.local:~/raccbuddy/
   ```

2. **On your Umbrel**, navigate to the backup directory:
   ```bash
   cd ~/raccbuddy/backups/20260223_120000/
   ```

3. **Create a `.env` file** with your secrets:
   ```bash
   cat > .env << EOF
   TELEGRAM_BOT_TOKEN=your-bot-token
   OWNER_TELEGRAM_ID=your-telegram-id
   OWNER_WHATSAPP_NUMBER=your-whatsapp-number
   EOF
   ```

4. **Start the services** (uses the docker-compose.yml from backup):
   ```bash
   docker compose up -d
   ```

5. **Restore your data**:
   ```bash
   # Wait for containers to be ready (30 seconds)
   sleep 30

   # Run the restore script
   ../../../restore-from-backup.sh .
   ```

#### Manual Restore (Alternative)

If you prefer to restore manually:

```bash
# Restore database
gunzip -c database.sql.gz | docker compose exec -T db psql -U raccbuddy raccbuddy

# Restore WhatsApp session (optional, to avoid re-scanning QR)
docker run --rm \
  -v raccbuddy_wa-session:/data \
  -v $(pwd):/backup \
  alpine sh -c "cd /data && tar xzf /backup/whatsapp-session.tar.gz"

# Restart services
docker compose restart
```

#### Using Pre-built Images Without Backup

If you just want to pull and use the latest images:

1. Update your `docker-compose.yml`:
   ```yaml
   app:
     image: your-dockerhub-username/raccbuddy-app:latest
     # Remove the 'build: .' line

   whatsapp:
     image: your-dockerhub-username/raccbuddy-whatsapp:latest
     # Remove the 'build: ./whatsapp-service' line
   ```

2. Start normally:
   ```bash
   docker compose up -d
   ```

#### Environment Variables for Production

For production deployments, consider these additional settings:

```dotenv
# Production database (if using external PostgreSQL)
DATABASE_URL=postgresql+asyncpg://user:pass@db-host:5432/raccbuddy

# Use xAI with function calling for better results
LLM_PROVIDER=xai
XAI_API_KEY=your-xai-api-key

# Or use Ollama on Umbrel (if installed)
OLLAMA_BASE_URL=http://umbrel.local:11434

# Increase memory retention
OWNER_MEMORY_RETENTION_DAYS=365

# More frequent nudges
NUDGE_CHECK_INTERVAL_MINUTES=30
```

#### Updating Deployed Images

To update your deployment with new code:

1. Rebuild and push on your dev machine:
   ```bash
   ./deploy-to-dockerhub.sh
   ```

2. On your server, pull latest and restart:
   ```bash
   docker compose pull
   docker compose up -d
   ```

---

## Platform Integrations

### Telegram (Built-in)

The primary interface. Just message your bot:

```
/start - Initialize and introduce Raccy
/help - Show available commands
```

Then chat naturally — Raccy remembers context and helps you stay connected.

### WhatsApp Bridge

Run the Node.js bridge service to connect WhatsApp:

```bash
cd whatsapp-service
npm install
npm start
```

1. A QR code will appear — scan it with WhatsApp (Linked Devices)
2. Session is saved locally, so you only scan once
3. All WhatsApp messages flow into the same AI pipeline as Telegram

**With Docker:**

```bash
docker compose up whatsapp
```

See [whatsapp-service/README.md](whatsapp-service/README.md) for details.

On startup the bridge also backfills the newest window of each chat
(`chat.fetchMessages`). WhatsApp Web cannot see the entire archive; the
cap is `WHATSAPP_HISTORY_MAX_PER_CHAT` (default 500). Re-running the
backfill does not duplicate rows. Limits, cursors, and the Telegram
Desktop export path (the Bot API has no history method) are in
[docs/sync.md](docs/sync.md).

### Adding More Platforms

IMAP and Matrix are optional in-process connectors. They are off until
`IMAP_ENABLED` or `MATRIX_ENABLED` is set. Setup, history limits, and the
providers that were looked at and not built (Signal, Slack, Discord,
iMessage/SMS) are in [docs/connectors.md](docs/connectors.md).

A bridge in another language POSTs to the REST API. When `API_SECRET_KEY`
is set, include the key in every request:

```bash
POST /api/messages
Content-Type: application/json
X-API-Key: your-api-secret-key

{
  "platform": "your_platform",
  "chat_id": "unique_chat_id",
  "from_id": "user_id",
  "contact_name": "John Doe",
  "text": "Hello!",
  "timestamp": "2026-02-21T10:00:00Z",
  "external_message_id": "provider-message-id",
  "is_group": false
}
```

---

## Skills & Extensibility

### Chat Skills

Customize how Raccy responds in conversations. Create a file in `skills/`:

```python
from src.core.skills.chat import BaseChatSkill, register_chat_skill

class CustomSkill(BaseChatSkill):
    name = "custom_skill"
    description = "My custom chat behavior"

    system_prompt_fragment = (
        "Always end responses with a raccoon fact."
    )

    @property
    def tool_schemas(self):
        # Optional: expose custom tools
        return []

    async def execute_tool(self, tool_name: str, args: dict):
        # Handle custom tool calls
        pass

register_chat_skill(CustomSkill())
```

### Nudge Skills

Create proactive reminders. Add a file in `nudges/`:

```python
from src.core.skills.base import BaseNudgeSkill, NudgeCheck, register_skill
import datetime

class MorningMotivation(BaseNudgeSkill):
    name = "morning_motivation"
    trigger = "morning"
    default_prompt = "Send a motivational morning message. Max 2 sentences."

    @property
    def cooldown_minutes(self):
        return 24 * 60  # Once per day

    async def should_fire(self, owner_id: int) -> NudgeCheck:
        now = datetime.datetime.now()
        if now.hour == 8:  # 8 AM
            return NudgeCheck(fire=True, reason="Morning time!")
        return NudgeCheck(fire=False)

register_skill(MorningMotivation())
```

Skills are automatically loaded at startup from `skills/` and `nudges/` folders.

---

## Tech Stack

**Core**
- Python 3.12 with full async/await (asyncio)
- python-telegram-bot v21+ for Telegram integration
- FastAPI for REST API endpoints
- SQLAlchemy 2.0 with async support
- **Alembic** for database migrations

**Database & AI**
- PostgreSQL 16 with pgvector extension
- Semantic embeddings for efficient context retrieval
- **Owner memory deduplication** (cosine similarity > 0.9 → merge)
- Ollama for local LLM inference (default: llama3.2:3b)
- xAI Grok API support for cloud inference with function calling
- **Sentiment / mood analysis** via Ollama

**State & Persistence**
- **Persistent user & contact state** (DB-backed, survives restarts)
- **DB-backed scheduled jobs** (restored on restart)
- In-memory write-through cache for low-latency state access

**Platform Bridges**
- Node.js + whatsapp-web.js for WhatsApp
- REST API for custom integrations

**DevOps**
- Docker & Docker Compose
- pytest for testing
- black + ruff for code formatting

---

## Project Structure

```
raccbuddy/
├── src/
│   ├── bot.py                    # Telegram bot entrypoint
│   ├── api.py                    # REST API for external bridges
│   ├── summarizer.py             # Background conversation summarizer
│   ├── core/
│   │   ├── auth.py               # Owner authentication
│   │   ├── config.py             # Settings management
│   │   ├── plugin_loader.py      # Dynamic plugin loading
│   │   ├── db/
│   │   │   ├── models.py         # SQLAlchemy models (pgvector)
│   │   │   ├── crud.py           # Database queries
│   │   │   └── session.py        # Engine & session factory
│   │   ├── agentic/
│   │   │   ├── __init__.py       # Init/shutdown lifecycle
│   │   │   ├── checkpointer_registry.py  # Pluggable LangGraph checkpointers
│   │   │   ├── engine.py         # Proactive cycle orchestration
│   │   │   ├── graph.py          # 4-node StateGraph (LangGraph)
│   │   │   ├── metrics.py        # Prometheus counters/histograms
│   │   │   ├── state.py          # AgenticState TypedDict
│   │   │   ├── tools.py          # Skill/tool adapters for graph nodes
│   │   │   └── tracing.py        # Langfuse tracing integration
│   │   ├── habits/
│   │   │   └── detector.py       # Habit detection (frequency + LLM)
│   │   ├── llm/
│   │   │   ├── base.py           # LLM provider interface
│   │   │   ├── interface.py      # LLM facade
│   │   │   └── providers/
│   │   │       ├── ollama.py     # Ollama provider
│   │   │       └── xai.py        # xAI/Grok provider
│   │   ├── memory/
│   │   │   ├── base.py           # PostgreSQL memory backend (pgvector)
│   │   │   └── context_builder.py # Layered, token-budgeted context assembly
│   │   ├── nudges/
│   │   │   └── engine.py         # Nudge execution engine
│   │   ├── relationship/
│   │   │   └── manager.py        # Dynamic relationship scoring
│   │   ├── scheduled/
│   │   │   └── jobs.py           # DB-backed scheduled messages
│   │   ├── sentiment/
│   │   │   └── analyzer.py       # Mood / sentiment analysis
│   │   ├── skills/
│   │   │   ├── base.py           # Nudge skill base & registry
│   │   │   ├── chat.py           # Chat skill system & registry
│   │   │   ├── loader.py         # Skill auto-discovery
│   │   │   └── nudge.py          # Built-in nudge skills
│   │   ├── state/
│   │   │   └── persistent.py     # Persistent state management
│   │   ├── voice/
│   │   │   ├── __init__.py       # Public API (voice_manager singleton)
│   │   │   ├── base.py           # STT/TTS abstract base classes
│   │   │   ├── manager.py        # VoiceManager orchestrator
│   │   │   └── providers/
│   │   │       ├── whisper_stt.py # Whisper STT provider
│   │   │       └── bark_tts.py   # Bark TTS provider
│   │   └── tools/
│   │       └── registry.py       # LLM function calling tools
│   ├── handlers/
│   │   ├── chat.py               # Message handling + enrichment
│   │   ├── start.py              # /start command
│   │   └── voice.py              # Voice message handler
│   └── plugins/
│       └── base.py               # Plugin base class
├── alembic/                      # Database migration scripts
│   ├── env.py
│   └── versions/
├── skills/                       # Custom chat skills (auto-loaded)
│   └── example_motivation.py
├── nudges/                       # Custom nudge skills (auto-loaded)
│   └── example_weekend.py
├── plugins/                      # Custom platform plugins (auto-loaded)
│   └── example_echo.py
├── scripts/
│   ├── deploy-to-dockerhub.sh    # Multi-platform build + push + backup
│   └── restore-from-backup.sh    # Restore DB + WhatsApp session from backup
├── whatsapp-service/             # WhatsApp bridge (Node.js)
│   ├── src/
│   │   ├── index.js              # whatsapp-web.js setup
│   │   └── forwarder.js          # Forward to Python API
│   └── Dockerfile
├── tests/                        # Unit tests
├── alembic.ini                   # Alembic configuration
├── docker-compose.yml            # PostgreSQL + WhatsApp service
├── requirements.txt
└── README.md
```

---

## API Reference

### REST API Endpoints

#### `POST /api/messages`

Receive messages from external platform bridges.

**Authentication:** When `API_SECRET_KEY` is set in `.env`, all requests must include an `X-API-Key` header matching the configured secret. Requests without the correct key receive `401 Unauthorized`. Leave `API_SECRET_KEY` empty to disable auth (local/LAN use only).

**Headers:**
```
X-API-Key: your-api-secret-key
Content-Type: application/json
```

**Request Body:**
```json
{
  "platform": "whatsapp",
  "chat_id": "1234567890@c.us",
  "from_id": "0987654321@c.us",
  "contact_name": "Alice",
  "text": "Hey, how are you?",
  "timestamp": "2026-02-21T10:30:00Z",
  "is_group": false,
  "group_name": null
}
```

**Response:** `200 OK` with `{"status": "ok"}`

#### `GET /health`

Health check endpoint.

**Response:** `{"status": "ok"}`

### LLM Tools

Available when using advanced providers (xAI):

- `analyze_contact(contact_name)` - Run relationship analysis
- `get_insights(contact_name)` - Get conversation insights
- `get_relationship_score(contact_name)` - Get score (0-100)
- `list_contacts()` - List all contacts
- `summarize_contact(contact_name, days)` - Summarize recent history
- `schedule_message(contact_name, when, message)` - Schedule future message

---

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | *(required)* | Bot token from @BotFather |
| `OWNER_TELEGRAM_ID` | `0` | Your Telegram user ID (auto-set on `/start`) |
| `DATABASE_URL` | *(required)* | PostgreSQL connection string |
| `LLM_PROVIDER` | `ollama` | `ollama` or `xai` |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_MODEL` | `llama3.2:3b` | Model for text generation |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | Model for embeddings |
| `XAI_API_KEY` | *(optional)* | xAI API key (if using xAI) |
| `XAI_MODEL` | `grok-4-1-fast-reasoning` | xAI model name |
| `MAX_CONTEXT_TOKENS` | `30000` | Maximum LLM context size (Ollama KV-cache + input budget) |
| `EMBED_DIMENSIONS` | `768` | Embedding vector dimensions (must match embed model) |
| `API_SECRET_KEY` | *(empty)* | X-API-Key secret for `POST /api/messages`; leave empty to disable auth |
| `MAX_SUMMARY_WORDS` | `150` | Summary length limit |
| `NUDGE_CHECK_INTERVAL_MINUTES` | `60` | How often to check for nudges |
| `SENTIMENT_MODEL` | `llama3.2:3b` | Model used for mood classification |
| `MEMORY_RECENT_MESSAGES` | `15` | Number of recent messages included in every context window |
| `MEMORY_SEMANTIC_CHUNKS` | `6` | pgvector semantic chunks retrieved per query |
| `MEMORY_MAX_SUMMARIES` | `3` | Past daily summaries included in context |
| `REL_WEIGHT_FREQUENCY` | `0.30` | Weight for message frequency in relationship scoring |
| `REL_WEIGHT_RECENCY` | `0.30` | Weight for recency in relationship scoring |
| `REL_WEIGHT_SENTIMENT` | `0.25` | Weight for sentiment in relationship scoring |
| `REL_WEIGHT_REPLY_RATE` | `0.15` | Weight for reply rate in relationship scoring |
| `OWNER_MEMORY_RETENTION_DAYS` | `90` | Days before low-importance memories are pruned |
| `DB_POOL_SIZE` | `10` | SQLAlchemy connection pool size |
| `DB_MAX_OVERFLOW` | `20` | Maximum overflow connections |
| `MAX_TOOL_ROUNDS` | `10` | Max LLM tool-call loop iterations |
| `VOICE_ENABLED` | `false` | Enable voice message handling (requires ffmpeg + torch) |
| `VOICE_REPLY_MODE` | `text` | Reply mode for voice messages: `text`, `voice`, or `both` |
| `VOICE_LANGUAGE` | *(empty)* | Force transcription language (ISO-639-1, e.g. `en`); empty = auto-detect |
| `STT_PROVIDER` | `whisper` | Speech-to-text provider |
| `STT_MODEL` | `openai/whisper-small` | HuggingFace model ID for STT |
| `TTS_PROVIDER` | `bark` | Text-to-speech provider |
| `TTS_MODEL` | `suno/bark-small` | HuggingFace model ID for TTS |
| `TTS_VOICE_PRESET` | `v2/en_speaker_6` | Bark voice preset |
| `AGENTIC_ENABLED` | `false` | Enable the proactive agentic cycle (LangGraph) |
| `CHECKPOINTER_BACKEND` | `postgres` | LangGraph checkpointer: `postgres` or `sqlite` |
| `MAX_CYCLE_TOKENS` | `8192` | Token budget per agentic cycle |
| `AGENTIC_CYCLE_INTERVAL_MINUTES` | `30` | How often the agentic cycle runs |
| `LANGFUSE_ENABLED` | `false` | Enable Langfuse tracing for agentic cycles |
| `LANGFUSE_PUBLIC_KEY` | *(empty)* | Langfuse public key |
| `LANGFUSE_SECRET_KEY` | *(empty)* | Langfuse secret key |
| `LANGFUSE_HOST` | `http://localhost:3000` | Langfuse server URL |
| `PROMETHEUS_ENABLED` | `false` | Enable Prometheus metrics endpoint |
| `PROMETHEUS_PORT` | `9090` | Prometheus metrics HTTP server port |
| `XAI_ENABLE_BUILTIN_TOOLS` | `false` | Enable Grok built-in tools (web/X/code search — data leaves machine) |

### Recommended Ollama Models

**For 8GB+ RAM:**
- `llama3.2:3b` (default, fast)
- `qwen2.5:7b` (better quality)

**For 16GB+ RAM:**
- `llama3.1:8b`
- `mistral:7b`

---

## FAQ

### General Questions

**Q: Is RaccBuddy really private?**
A: Yes! By default, everything runs locally on your machine. Messages, relationships, and habits stay in your local PostgreSQL database. If you use Ollama (default), even LLM inference happens locally. Only if you choose to use xAI does data leave your machine (and only the conversation context assembled by ContextBuilder — never raw message archives or relationship data).

**Q: How much does it cost to run?**
A: RaccBuddy is free and open source. If using local Ollama, there are zero ongoing costs (just electricity). If you opt for xAI Grok, you'll pay xAI's API rates.

**Q: What hardware do I need?**
A:
- **Minimum**: 8GB RAM, any modern CPU (for llama3.2:3b)
- **Recommended**: 16GB RAM (for qwen2.5:7b or larger models)
- **Storage**: ~2GB for Docker images + models

**Q: Can I use RaccBuddy on Windows?**
A: Yes! Docker works on Windows. For local Python setup, use WSL2 for the best experience.

**Q: Does RaccBuddy work offline?**
A: The bot requires internet to receive Telegram/WhatsApp messages, but LLM processing can be 100% offline with Ollama. The database runs locally regardless.

### Features

**Q: What platforms are supported?**
A: Telegram (native) and WhatsApp (bridge). Optional IMAP and Matrix pull connectors are in the repo and off by default. See [docs/connectors.md](docs/connectors.md).

**Q: Can multiple people use one instance?**
A: Not yet. RaccBuddy currently locks to one owner (the first person to send `/start`). Multi-user support is on the roadmap.

**Q: What languages does RaccBuddy understand?**
A: It depends on your LLM. Llama3.2 and Qwen2.5 support many languages. Raccy's personality prompts are in English, but it should respond in whatever language you use.

**Q: Does it support voice messages?**
A: Yes! Set `VOICE_ENABLED=true` in your `.env`. RaccBuddy transcribes voice notes locally via Whisper and can reply with synthesized speech via Bark. Requires `ffmpeg` and optional `torch`/`transformers` dependencies (see `requirements.txt`).

### Technical

**Q: Why PostgreSQL instead of SQLite?**
A: We use pgvector for semantic search on embeddings, which requires PostgreSQL. It's also more scalable for production use.

**Q: Can I self-host this on a VPS?**
A: Absolutely! The Docker setup works on any Linux VPS. Just make sure to:
- Use strong passwords for PostgreSQL
- Configure firewall rules if exposing the API
- Consider using xAI instead of Ollama if the VPS has limited RAM

**Q: How do I back up my data?**
A: Your data is in the PostgreSQL database. Backup options:
```bash
# Export database
docker exec $(docker ps -qf "name=raccbuddy-db") pg_dump -U raccbuddy raccbuddy > backup.sql

# Restore
cat backup.sql | docker exec -i $(docker ps -qf "name=raccbuddy-db") psql -U raccbuddy -d raccbuddy
```

**Q: Can I use OpenAI or Anthropic instead of Ollama/xAI?**
A: Not built-in yet, but it's easy to add! Check `src/core/llm/providers/` and implement a new provider following the base interface. PRs welcome!

**Q: How does RaccBuddy manage context size?**
A: RaccBuddy uses the `ContextBuilder` pipeline, which assembles up to 30,000 tokens (configurable) from multiple layers:
1. **Recent messages** — last N messages for immediate conversational continuity
2. **Semantic search** — pgvector retrieves only the most relevant past chunks
3. **Daily summaries** — compressed long-term memory without raw message bloat
4. **Owner personal facts** — deduplicated self-knowledge about the user
5. **Token budgeting** — each layer has a configured fraction of the total budget, preventing runaway context growth

### Privacy & Security

**Q: Can I see what data is stored?**
A: Yes! Connect to your database:
```bash
docker exec -it $(docker ps -qf "name=raccbuddy-db") psql -U raccbuddy -d raccbuddy
```
Then explore tables: `\dt` (list tables), `SELECT * FROM messages LIMIT 10;`, etc.

**Q: How do I delete all my data?**
A:
```bash
docker compose down -v  # Stops containers and deletes volumes
```

**Q: Can Telegram see my messages?**
A: Yes, Telegram delivers messages to your bot. If privacy is critical, consider hosting a Matrix bridge instead (fully self-hosted messaging).

**Q: What's sent to xAI if I use that provider?**
A: Only:
- System prompt (Raccy's personality)
- Current conversation context assembled by ContextBuilder (up to `MAX_CONTEXT_TOKENS`, default 30,000 — a budget-controlled subset of your data)
- Tool schemas (function definitions)
Never: full raw message history, raw embeddings, or relationship tables.

### Extending & Customizing

**Q: How do I change Raccy's personality?**
A: Edit the system prompt in `src/core/llm/interface.py`. Look for the main system prompt construction.

**Q: Can I add custom commands?**
A: Yes! Add command handlers in `src/handlers/` and register them in `src/bot.py`.

**Q: How do I create a custom nudge?**
A: Create a new file in the `nudges/` folder. See [Skills & Extensibility](#skills--extensibility) and check `nudges/example_weekend.py` for a template.

**Q: Can I disable certain features?**
A: Most features can be controlled via environment variables. Check [Configuration](#configuration).

### Development

**Q: How do I contribute?**
A: See the [Contributing](#contributing) section! We welcome PRs, bug reports, and feature requests.

**Q: Where are the tests?**
A: In the `tests/` folder. Run with `pytest tests/ -v`.

**Q: How do I add a new LLM provider?**
A: Create a new provider class in `src/core/llm/providers/` that implements the base interface. See `ollama.py` and `xai.py` for examples.

---

## Roadmap

- [x] **MVP**: Telegram bot with memory and summaries
- [x] **Phase 1**: WhatsApp integration via Node.js bridge
- [x] **Phase 2**: Multi-LLM provider support (Ollama + xAI)
- [x] **Phase 3**: Skills system (chat + nudge)
- [x] **Phase 4**: LLM function calling / tool use
- [x] **Phase 5**: Persistent state, Alembic migrations, dynamic relationship scoring, mood detection, habit detection, owner memory deduplication
- [x] **Phase 6**: Voice message support (Whisper STT + Bark TTS, local processing)
- [x] **Phase 6b**: Agentic proactive core (LangGraph, quality-gated nudges, Langfuse/Prometheus)
- [ ] **Phase 7**: Multi-user support (family/team mode)
- [ ] **Phase 8**: Signal/Discord/Matrix integrations
- [ ] **Phase 9**: Web dashboard for insights and configuration
- [ ] **Phase 10**: Plugin marketplace with community skills

**Want to influence the roadmap?** Open a [Discussion](https://github.com/dcbert/raccbuddy/discussions) or [Issue](https://github.com/dcbert/raccbuddy/issues)!

---

## Security & Privacy

RaccBuddy is built with **privacy as the foundation**:

### 🔒 What Stays Local
- **All your data**: Messages, contacts, relationships, habits, embeddings
- **Database**: Runs on your machine via Docker or local PostgreSQL
- **LLM inference**: Uses local Ollama by default (no data sent to cloud)
- **Embeddings**: Generated locally with `nomic-embed-text`

### ☁️ Optional Cloud Services
If you choose to use `LLM_PROVIDER=xai`:
- Only the **ContextBuilder-assembled context** (up to `MAX_CONTEXT_TOKENS`, default 30,000 — a token-budgeted subset) is sent to xAI
- No raw message archives, no raw embeddings, no relationship tables
- You control what's shared via environment variables and budget ratios

### 🛡️ Security Best Practices
- **Never commit `.env`** to version control (already in `.gitignore`)
- **Rotate your Telegram bot token** if exposed
- **Use strong PostgreSQL passwords** in production
- **Run behind a firewall** if exposing the REST API
- **Review the code** — it's open source for complete transparency

### 🔐 Data Retention
- Old conversation details are summarized to save space
- Owner memories are retained for 90 days by default (configurable via `OWNER_MEMORY_RETENTION_DAYS`)
- You can delete the database anytime: `docker compose down -v`

---

## Getting Help & Community

### Found a Bug?
Open an issue on [GitHub Issues](https://github.com/dcbert/raccbuddy/issues) with:
- RaccBuddy version / commit hash
- Operating system
- Steps to reproduce
- Relevant logs (remove sensitive data!)

### Have a Feature Request?
We'd love to hear your ideas! Open a [GitHub Issue](https://github.com/dcbert/raccbuddy/issues) with the `enhancement` label.

### Need Support?
- **Check the [Troubleshooting](#troubleshooting) section** above
- **Review existing [GitHub Issues](https://github.com/dcbert/raccbuddy/issues)**
- **Start a [Discussion](https://github.com/dcbert/raccbuddy/discussions)** for questions

### Beta Testing
As a beta tester, your feedback is invaluable:
- Report bugs, no matter how small
- Share feature ideas and use cases
- Contribute improvements via pull requests
- Help us improve documentation

---

## Contributing

We welcome contributions! Here's how:

### Quick Start for Contributors

1. **Fork** the repository on GitHub
2. **Clone** your fork:
   ```bash
   git clone https://github.com/YOUR_USERNAME/raccbuddy.git
   cd raccbuddy
   ```
3. **Create a branch**:
   ```bash
   git checkout -b feat/amazing-feature
   ```
4. **Set up your environment**: Follow [Installation Method 2](#installation-method-2-local-python-advanced)
5. **Make your changes**
6. **Run tests**:
   ```bash
   pytest tests/ -v
   ```
7. **Commit** with [conventional commits](https://www.conventionalcommits.org/):
   ```bash
   git commit -m "feat: add amazing new feature"
   ```
8. **Push** to your fork:
   ```bash
   git push origin feat/amazing-feature
   ```
9. **Open a Pull Request** on GitHub

### Coding Standards

- **Read the project instructions**: [.github/copilot-instructions.md](.github/copilot-instructions.md)
- **Python 3.12+** with full type hints
- **Use `async/await`** for I/O operations
- **Follow black + ruff** formatting (run `black . && ruff check .`)
- **Write tests** for new features (see `tests/` folder)
- **Use `ContextBuilder`** for all LLM calls — never build prompts manually (uses summaries + semantic retrieval + token budgets)
- **Document your code** with clear docstrings

### Commit Message Format

We use [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` — New feature
- `fix:` — Bug fix
- `docs:` — Documentation changes
- `test:` — Adding or updating tests
- `refactor:` — Code restructuring
- `chore:` — Maintenance tasks

### Areas We Need Help
- 🌐 **Platform bridges**: Signal, Discord, Matrix, iMessage
- 🎯 **Nudge skills**: More proactive reminder types
- 🔒 **Privacy audits**: Security reviews and pen testing
- ⚡ **Performance**: Token efficiency, query optimization
- 📚 **Documentation**: Tutorials, guides, API docs
- 🧪 **Testing**: Expand test coverage, integration tests
- 🎨 **Design**: Logo improvements, UI mockups for future web interface

### Development Tools

**Linting & Formatting:**
```bash
black .
ruff check .
```

**Type Checking:**
```bash
mypy src/
```

**Running Tests:**
```bash
pytest tests/ -v --tb=short
pytest tests/test_memory.py -v  # Single test file
```

**Database Migrations:**
```bash
# Create a new migration
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head

# Rollback one migration
alembic downgrade -1
```

---

## License

MIT — see the [LICENSE](LICENSE) file for details.

---

<div align="center">
  <p>Made with ❤️ in Trento, Italy</p>
  <p>🦝 <strong>Stay connected. Stay mindful. Stay raccoon.</strong> 🦝</p>
</div>
