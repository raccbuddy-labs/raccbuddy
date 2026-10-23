# RaccBuddy codebase review — October 2026

Hardware context for this review: the owner now runs an NVIDIA RTX 5080 with 16 GB of VRAM. The tree that was reviewed is `main` at `66b3d7c` ("Enhance chat handle, fix XAI provider issues, improved job scheduling"). Findings below are from the code, migrations, tests, changelog, and git history in this repository. Third-party VRAM figures are cited as estimates and were not measured on the owner's machine.

## Executive summary

RaccBuddy is a single-owner, privacy-first companion. Telegram is an in-process bot. WhatsApp is a Node bridge that POSTs live messages to FastAPI. PostgreSQL stores chats, scores, and pgvector memories. An opt-in LangGraph cycle drafts nudges. That shape is sound for one person. It will not survive a real backfill or a year of multi-platform history, and several "quality" guardrails exist only because the default model is `llama3.2:3b`.

The history bug is real, and it is broader than a missing webhook. Every connector only observes messages that arrive after it starts listening. There is no history fetch, no provider message id, and no sync cursor. `POST /api/messages` also derives `chat_id` from Python's salted `hash()`, so the same WhatsApp chat does not even stay stable across process restarts. Downstream, the daily summarizer only looks at today and at most 20 messages, and there is no pgvector index at all.

Umbrel in this repo is a deploy target (`README.md` "Deploying to Umbrel"), not a chat connector. A search of the tree, changelog, and `git log` found no Hermes integration and no mention of Nous Hermes. The model-quality workarounds that do exist are aimed at `llama3.2:3b` on a small home server: a 30,000-token `num_ctx` that Ollama used to ignore, a first-word `APPROVE`/`DISCARD` reflector, a two-line sentiment parser, and tool-calling treated as unreliable. On a 16 GB 5080 those small-model compromises are no longer the binding constraint. A Qwen3 8B or 14B class model (Q4) fits and can do structured output and tool calls. Qwen3 32B Q4 does not fit in 16 GB with a usable context. The 30,000-token default still does not fit a 14B model at Q4 once the KV cache is counted. The LangGraph split between deterministic nudge checks and LLM drafting should stay. The reflector's string match should not.

Embeddings are `nomic-embed-text` at 768 dimensions, truncated to 512 characters, one vector per call, with sequential scans. `qwen3-embedding:0.6b` is the practical upgrade on this GPU. Keeping 768 dimensions via Matryoshka avoids a migration; moving to the native 1024 requires a full re-embed. Add an HNSW index either way. Do not run the 8B embedding model beside a 14B chat model.

## What this repository actually is

| Path | Role |
| --- | --- |
| `src/bot.py` | Telegram polling plus a FastAPI server in a daemon thread |
| `src/api.py` | `POST /api/messages` for bridges |
| `whatsapp-service/` | whatsapp-web.js live forwarder |
| `src/plugins/base.py`, `plugins/` | Plugin base class. WhatsApp does not use it |
| `src/core/memory/` | ContextBuilder and pgvector memories |
| `src/core/agentic/graph.py` | ContextKeeper → NudgePlanner → Crafter → Reflector |
| `src/summarizer.py` | One summary per contact per calendar day |

There is no `docs/` tree besides this brief. Contributor rules that disagree with the code are called out under P1.

## Hardware reassessment

### Old complaints and the workarounds still in the tree

These are the model-quality constraints that shaped the code. They were written for an 8 GB-class Umbrel box running `llama3.2:3b`, not for a 5080.

| Workaround | Where | Why it was added | Does it still hold on a 16 GB 5080? |
| --- | --- | --- | --- |
| Default chat model `llama3.2:3b` | `src/core/config.py` lines 50–51, README "Recommended Ollama Models" | Fits ~8 GB RAM, CPU-friendly | No. It is the wrong default. Qwen3 8B Q4 (~5 GB) or Qwen3 14B Q4 (~9–11 GB) fits and follows instructions, tools, and JSON far more reliably. |
| `num_ctx` forced to `MAX_CONTEXT_TOKENS` (default 30,000) | `src/core/llm/providers/ollama.py` lines 70–73 and 108–110; changelog 0.2.0 | Ollama silently used a 2,048-token KV cache and replies fell apart | The bug fix (always send `num_ctx`) still holds. The 30,000 default does not. Third-party estimates (ModelFit, Oct 2026; VRAM Guide) put Qwen3 14B Q4_K_M weights around 9–11 GB. One estimate says 16k context fits and 32k does not (~17 GB); another says Q4_K_M can reach 32k. Treat 8k–16k as the safe fully-on-GPU window until measured here. `num_batch: 64` on the same call is a small-RAM knob and can go up once the model is resident. |
| Reflector accepts a nudge only if the first word is `APPROVE` | `src/core/agentic/graph.py` lines 149–207 | Small models would not return structured verdicts | The separate quality check can stay as a product choice. The string match should go. Qwen3 also has a thinking mode; leaked reasoning makes "first word" parsing worse, not better. Use JSON schema / Ollama structured output (`send: bool`, `reason: str`). |
| Sentiment is "two lines: a mood word and a number", then a hand map of valences | `src/core/sentiment/analyzer.py` lines 47–69 and 109–120 | `llama3.2:3b` would not reliably emit JSON | No longer justified as a model limitation. A one-shot JSON object is enough. Running that call on every live message is still a throughput problem (see P1). |
| Tool calling documented as an xAI feature, while `OllamaProvider.supports_tools` is unconditionally `True` | README "LLM Tool Calling"; `src/core/llm/providers/ollama.py` line 44; system prompt "CRITICAL RULE" in `src/core/llm/interface.py` lines 27–28 | 3B models loop tool calls and double-schedule jobs | Qwen2.5/Qwen3 7B+ tool calling is reliable enough to drop the "local models cannot use tools" split. Keep a hard `max_tool_rounds` cap. The prompt-level "never schedule twice" rule can shrink to an idempotent tool implementation. |
| Copilot rule "keep LLM context under 2000 tokens" vs config default 30,000 | `.github/copilot-instructions.md` line 10; `CONTRIBUTING.md` "2000 tokens max"; `src/core/config.py` line 74 | Stale instruction from the first token budget | The layered ContextBuilder should stay (raw history must not be pasted into the prompt). The 2,000-token rule is obsolete. Pick a budget the loaded model can actually hold: about 8,192–16,384 for Qwen3 14B Q4 on this card. |
| Summaries capped at 20 messages and 100 characters each | `src/summarizer.py` lines 63–64 | Afraid of blowing a small context | Chunk the day into windows and summarize the windows. A 14B model can take a real window (on the order of 40 full messages), not 100-character clips. |
| Embeddings: `nomic-embed-text`, 768-d, input sliced to 512 characters | `src/core/config.py` lines 51 and 76–77; `src/core/memory/base.py` line 167 (`content[:512]`) | nomic is tiny and matched the 768-d column | nomic is still correct and tiny, and it is no longer the quality ceiling. See the pgvector section. |

Assumption: this review did not load models on the owner's 5080. VRAM totals below are from public estimate pages (ModelFit updated 2026-10-14, VRAM Guide, SpecPicks, Spheron, Ollama library cards) and they disagree by a few gigabytes. Measure with `nvidia-smi` during `ollama run` before changing the default.

### What fits in 16 GB, and the tradeoff

Chat (Ollama GGUF, one model resident, display and runtime overhead left on the card):

| Model | Rough weight | Fits with context? | Tool calling / JSON | Recommendation |
| --- | --- | --- | --- | --- |
| `llama3.2:3b` (current default) | ~2 GB | Yes, including long context | Weak. This is why the guardrails exist | Stop using it as the default |
| `qwen3:8b` Q4_K_M | ~5 GB | Yes, 16k–32k is comfortable | Strong | Best daily driver if you also want a larger embedding model resident |
| `qwen3:14b` Q4_K_M | ~9–11 GB | Yes at 8k–16k. 32k is the disputed edge | Strong | Best quality that still fits. Set `MAX_CONTEXT_TOKENS` to 8192 or 16384, not 30000 |
| `qwen3:32b` Q4_K_M | ~19–20 GB weights alone | No, not with KV cache | Strongest dense open model in the family | Do not use on this card. CPU offload will feel broken |
| `qwen3:30b` (30B-A3B MoE) | ~19 GB download | Weights alone miss 16 GB | Strong, few active params | Assume it does not fit. Not verified here |

Qwen3 0.6B–4B also fit and are useful for the sentiment classifier so the chat model is not reloaded for a two-line label. Ollama loads one model at a time unless both fit; a 14B chat model plus `qwen3-embedding:0.6b` (~0.6–1.2 GB) fits, plus `qwen3-embedding:4b` (~2.5 GB) is tight beside 14B, and `qwen3-embedding:8b` (~4.7 GB Q4) does not fit beside 14B.

### What this means for the LangGraph design

The graph is four nodes with a supervisor that only reads `next_node` (`src/core/agentic/graph.py` lines 216–270). NudgePlanner does not call a model. It runs the registered skill checks. That split is good design, not a small-model crutch: skills stay testable and the model cannot invent a nudge that no skill fired.

What the small model forced:

- Crafter and Reflector are two full generations per candidate. Reflector then trusts the first whitespace-delimited token (`graph.py` lines 189–191). A Qwen3 8B/14B call with structured output can draft and decide in one step: `{ "text": "...", "send": true, "reason": "..." }`. Collapse Crafter and Reflector when `OLLAMA_MODEL` is a known tool-capable family. Keep Reflector as a second call only behind a config flag if you want a cheaper model to critique a stronger one.
- Do not collapse ContextKeeper into the LLM. It is the token budget. With a 16k window it matters more, not less, because a 14B model will happily consume whatever you send.
- `supports_tools = True` for every Ollama tag is now reasonable for Qwen3 8B/14B and still wrong for `llama3.2:3b`. Gate it on the model name, or require `OLLAMA_TOOLS_ENABLED` so a 3B tag cannot loop `schedule_message`.
- The checkpointed graph (Postgres or SQLite) does not need to change because the model got smarter.

### What this means for pgvector

Current design:

- Column type is `Vector(768)` baked into the initial migration (`alembic/versions/c617a5efbe23_initial_schema.py` lines 79 and 120) and into the ORM via `settings.embed_dimensions` captured at import (`src/core/db/models.py` lines 29–30 and 97).
- Queries use cosine distance (`src/core/db/crud.py` lines 351–360, `src/core/memory/base.py` around the hybrid search). There is no HNSW index and no IVFFlat index anywhere in `alembic/versions/` or `PostgresMemory.setup()`.
- Every embed call sends a single string, sliced to 512 characters. nomic's useful context is thousands of tokens; the slice throws that away. Ollama's nomic tag also often advertises a 2,048 context unless `num_ctx` is set on the embed request. This code never sets it (`ollama.py` `embed()`, lines 279–296).
- Nothing chunks a conversation. The only vectors are daily summaries and a few extracted memories. A backfill of a year of chats will not become searchable until those summaries exist, and the summarizer ignores every day except today.

Model choice on this GPU:

| Model | Dimensions | VRAM | Quality note | Schema impact |
| --- | --- | --- | --- | --- |
| `nomic-embed-text` (today) | 768 | ~0.3 GB | English-leaning, fine for short facts, weak multilingual | None |
| `qwen3-embedding:0.6b` with MRL truncated to 768 | 768 requested | ~0.6–1.2 GB | Clear step up, 32k context, 100+ languages. Truncation costs some of the native quality | No `ALTER` of vector columns. Still must re-embed every row; mixed nomic and Qwen vectors in one column are not comparable |
| `qwen3-embedding:0.6b` native | 1024 | same | Better than the 768 truncation | Migration: new column or alter type, rebuild index, re-embed |
| `qwen3-embedding:4b` | 2560 native, MRL can request 1024 or 768 | ~2.5 GB | Highest quality that still coexists with an 8B chat model. Tight next to 14B | Same migration if not using MRL 768 |
| `qwen3-embedding:8b` | 4096 | ~4.7 GB Q4, 15 GB FP16 | Best Ollama embedding in the Oct 2026 roundup that was checked. FP16 does not fit beside a chat model | Do not make this the default on a 16 GB card that also chats |
| `embeddinggemma` | 768 | ~0.6 GB | Drop-in dimension match, quality close to nomic rather than a new ceiling | Re-embed, no dimension change |

Recommendation: switch the default embed model to `qwen3-embedding:0.6b`, request 768 dimensions so existing columns keep working, re-embed in batches, and add HNSW. Move to 1024 only when you are ready to rewrite every vector. Do not raise dimensions in `.env` alone. The migration hardcodes `Vector(768)` and the ORM reads `EMBED_DIMENSIONS` once at import; a mismatch fails at query time.

Index: for a personal archive (thousands to a few hundred thousand vectors) use HNSW, not IVFFlat. IVFFlat needs a training row count and decays as you insert. A reasonable starting point on pgvector 0.5+ is `USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)` and `hnsw.ef_search = 40` at query time. Build the index after the re-embed, not before. Cosine ops match the queries already in the code.

Chunking: stop clipping at 512 characters. Chunk by conversation-day, or by ~400–800 tokens with a small overlap, and store the chunk as a `semantic_memories` row whose metadata has `platform`, `chat`, and the source timestamp range. One vector per day-summary can stay as a coarser layer. The 0.6B model can embed a full day of chat; do not embed every raw message (storage and index size grow without helping the ContextBuilder, which only pulls a handful of chunks).

Re-embed / migration path:

1. Add `embedding_model` and `content_hash` columns to `semantic_memories`, `owner_memories`, and `summaries`.
2. Keep writing 768-d vectors until the backfill of old rows finishes, so reads never mix spaces.
3. Background job: select rows where `embedding_model IS DISTINCT FROM` the configured model, embed in batches of 32–64, update, commit per batch.
4. Only then `CREATE INDEX ... hnsw`. Drop any sequential-scan-only assumption.
5. If you later move to 1024, add a new column (`embedding_1024`), dual-write, rebuild the index, then drop the 768 column in a later migration. In-place `ALTER` of a vector column is a rewrite.

Throughput: a backfill of 50k chunks is dominated by the one-at-a-time `embed()` HTTP call, not by HNSW. Batch the embedding request (`input: [text, ...]`) and cap concurrency at 1 while the chat model is loaded, or the 5080 will evict one of them.

## Prioritized findings

### P0 — breaks correctness now, or will corrupt a history backfill

**P0.1 Live listeners never read history, and nothing checkpoints**

- Location: `whatsapp-service/src/index.js` lines 103–163 (`client.on("message_create")` only). `src/bot.py` lines 259–266 (text/voice handlers, `run_polling()` with no persisted offset and no history call). `src/api.py` lines 89–170 (one message per POST, no cursor).
- Why it matters: messages that already exist when the bridge connects are invisible. Relationship scores, habits, and summaries are computed only from whatever arrived after the last start. Telegram's Bot API has no `getHistory`. WhatsApp Web can load earlier messages (`chat.fetchMessages`) and the bridge never calls it. Restarts do not resume a place in the chat; they only hear new events.
- Fix: give every provider message a stable id, store a per-chat cursor, backfill where the API allows, and dedupe on re-run. Telegram Bot API history must be documented as unavailable; a Desktop `result.json` import is the practical backfill. Details belong in the history-sync change, not here.

**P0.2 Provider message identity does not exist, so retries duplicate rows**

- Location: `src/core/db/models.py` `Message` lines 64–82 (no external id, no unique constraint). `src/core/db/crud.py` `save_message` lines 22–43 (always `INSERT`). `whatsapp-service/src/forwarder.js` lines 16–61 (retries the POST up to 3 times; a timeout after a successful commit inserts again).
- Why it matters: the WhatsApp retry loop and any future backfill will double-count the same text. Scores, mood rows, and summaries then drift.
- Fix: unique `(platform, external_chat_id, external_message_id)` (partial, so old rows stay valid). Upsert on that key. Treat a second delivery as a no-op, and a changed body with the same id as an edit that keeps the original `timestamp`.

**P0.3 `chat_id` for bridge messages is Python's salted `hash()`**

- Location: `src/api.py` lines 151–152: `hash(msg.chat_id) & 0x7FFFFFFFFFFFFFFF`.
- Why it matters: `hash()` is randomized per process (`PYTHONHASHSEED`). After a restart the same WhatsApp JID lands on a different `messages.chat_id`. Conversation history, which is keyed by that integer (`get_conversation_history`, `get_recent_messages`), splits in two. Deduping by chat id cannot work on top of this.
- Fix: hash with SHA-256 (or store the provider chat id as text and key on that). Telegram can keep the integer id Telegram already assigns.

**P0.4 Original send time is optional, so order becomes ingest order**

- Location: `src/api.py` lines 127–133 (parse failure becomes `None`). `src/core/db/crud.py` lines 38–39 (timestamp omitted → SQL `now()`). `src/handlers/chat.py` lines 112–118 (Telegram save does not pass `update.message.date` or `message_id`). `Message.timestamp` in `models.py` lines 78–82.
- Why it matters: a backfill inserted today would look like it all happened today. Recency scores, "contact went quiet" nudges, and daily summaries would all be wrong. Edits and out-of-order pages have nowhere to land except a new row at "now".
- Fix: require a provider timestamp for synced messages, store it separately from `ingested_at`, and order reads by `(timestamp, external_message_id)`. On edit, update `text` and `edited_at` and leave `timestamp` alone.

**P0.5 Owner-side WhatsApp messages are stored as if the contact sent them**

- Location: `src/api.py` lines 135–160. `is_owner_message` is computed from `from_me` / `OWNER_WHATSAPP_NUMBER` and then only interpolated into a log line. `from_contact_id` is always the contact row.
- Why it matters: frequency and recency (`src/core/relationship/manager.py` lines 66–84 and 90–100) count those rows as contact activity. A history backfill of a chatty thread will inflate the score with the owner's own outbound texts.
- Fix: persist `is_from_owner`. Keep the contact id so the thread still groups, and exclude owner rows from the contact-volume signals.

**P0.6 Daily summarizer cannot absorb a backfill**

- Location: `src/summarizer.py` lines 49–64 (today only, `msgs[:20]`, each clipped to 100 chars). `get_messages_since` default `limit=20` in `src/core/db/crud.py` lines 95–109. The 6-hour job in `src/bot.py` lines 114–117 calls that path only.
- Why it matters: importing a year of history writes raw rows and then embeds nothing for any day except today, and even today it sees 20 clipped lines. Pointing the backfill at the live enrich path instead would be worse: `chat_handler` calls the mood model once per message (`src/handlers/chat.py` lines 171–188, `src/core/sentiment/analyzer.py` `detect_mood`).
- Fix: after a backfill, summarize by `(contact, calendar day)` in bounded batches (a window of messages per LLM call, one embed per day, a max number of days per run, a pause between calls). Do not run per-message sentiment on historical rows. Let the periodic job drain the rest.

### P1 — will hurt at real volume, or leaves the 5080 unused

**P1.1 No vector index, and no index on `messages.timestamp`**

- Location: initial migration creates btree indexes on `chat_id` and `from_contact_id` only (`alembic/versions/c617a5efbe23_initial_schema.py` lines 65–71). Cosine search is `ORDER BY embedding <=> query` with no ANN index. `get_idle_contact_ids` (`crud.py` lines 116–128) groups the whole `messages` table.
- Why it matters: at a few thousand memories, sequential scan is fine. At a few hundred thousand (many chats, multi-year backfill, one chunk per day) every nudge cycle and every reply does a full cosine scan, and idle detection seq-scans messages. HNSW build cost is paid once; query cost is the thing that grows.
- Fix: btree `(from_contact_id, timestamp)` and `(chat_id, timestamp)`. HNSW on the three vector columns after the embed model is chosen. See the pgvector section for parameters.

**P1.2 High-importance message embedding writes columns that do not exist**

- Location: `src/core/memory/base.py` lines 135–148. `UPDATE messages SET embedding = :emb, importance = :imp`. `Message` has neither column (`models.py` lines 64–82). The exception is swallowed.
- Why it matters: `add_message(..., importance>=6)` looks like it indexes the raw message and never does. Callers get a silent warning. It is also the only per-message embed path, so "fixing" it without a column and an index would encourage the wrong design (one vector per raw message).
- Fix: delete this branch, or write a `semantic_memories` row through `add_semantic_memory`. Do not add an unindexed `messages.embedding` column.

**P1.3 Two event loops, two pools, and a log handler that drops records**

- Location: `src/bot.py` lines 214–237 (uvicorn in a daemon thread). `src/core/db/session.py` lines 35–65 (engine cached per loop id, never disposed). `src/core/db/log_handler.py` lines 46–63 (`create_task` only if `get_running_loop()` succeeds; the task is not retained).
- Why it matters: this was a real fix for asyncpg's loop affinity, and it is correct as far as it goes. A backfill on the API loop and the bot on the main loop then hold `pool_size + max_overflow` each (default 10+20, twice). Postgres on a small Umbrel is often at `max_connections=100`. The log handler silently discards warnings emitted off-loop, which is exactly when a bridge thread fails. Unretained tasks can also be garbage-collected mid-write.
- Fix: one shared process is fine, but cap the API pool separately (`DB_POOL_SIZE` for the bot, a smaller pool for the bridge). Dispose engines on shutdown. Persist logs via a queue owned by a single loop.

**P1.4 Plugin example calls `save_message` with arguments it does not accept**

- Location: `plugins/example_echo.py` lines 43–58 (`from_id=`, `contact_id=`). Signature is `src/core/db/crud.py` lines 22–30. `plugins/README.md` shows the same wrong call (lines 72–78).
- Why it matters: `load_user_plugins()` imports every module in `plugins/`, so this example is registered in production (`src/bot.py` lines 91–93). The first message it handles raises `TypeError`. New connectors copied from the README will do the same.
- Fix: point the README and the example at the ingest function once it exists. Do not auto-register the example, or gate it.

**P1.5 Reply-rate score is not per contact**

- Location: `src/core/relationship/manager.py` lines 135–175. Contact volume is per `from_contact_id`. "Owner replies" are every row in `chat_id == owner_id` with a null contact, which is the owner's Telegram DM with the bot, not replies inside that contact's thread.
- Why it matters: the 15% reply-rate weight (`config.py` lines 128–130) does not measure reciprocity. It mixes the owner's private chat with Raccy into every contact's score. A multi-platform history makes this louder.
- Fix: count owner-direction messages in the same thread (`external_chat_id` or `chat_id` plus `is_from_owner`) against contact-direction messages in that thread.

**P1.6 Sentiment, habits, and the agentic cycle assume a tiny model and a tiny day**

- Location: mood on every live message (`handlers/chat.py` 185–187). Habit LLM extraction walks summaries (`src/core/habits/detector.py`). Agentic cycle every 30 minutes (`config.py` 145–146) with an 8,192-token cycle budget. Reflector slices context with `chars/4` (`graph.py` lines 27–28 and 173–174).
- Why it matters: a 14B model on a 5080 is fast enough for a real window and too expensive to call once per SMS-sized message plus once per nudge plus once per reflection, all while an embed model is loaded. The `chars/4` estimate under-counts Qwen tokenization on multilingual chats, so the reflector silently sees a shorter context than `max_cycle_tokens` implies.
- Fix: batch sentiment (or skip it on backfill). One structured generation per nudge. Use the provider's token count when the API returns it (Ollama already logs `prompt_eval_count`).

**P1.7 Schema is created twice, and vector width is frozen at import**

- Location: `init_db` and `PostgresMemory.setup` both `create_all` (`src/core/db/session.py` lines 94–104, `memory/base.py` lines 100–106) while Alembic is also the migration path. `EMBED_DIMENSIONS = settings.embed_dimensions` at import (`models.py` lines 29–30).
- Why it matters: `create_all` will not alter a 768-d column if you change the env var, and it will not add a partial unique index you only put in Alembic if an old database was created by `create_all` first. Changing embed width in `.env` looks like it works until the first insert.
- Fix: treat Alembic as the only schema path in production. Fail startup if `embed_dimensions` does not match the column (read `atttypmod` or a `schema_meta` row).

**P1.8 Unbounded side tables**

- Location: `mood_entries`, `relationship_events`, `app_logs` (`models.py` lines 189–209, 212–229, 356–380). No retention job. Owner memories are pruned (`owner_memory_retention_days`); these are not.
- Why it matters: mood-on-every-message plus a backfill, if someone wires enrich into the ingest path, fills `mood_entries` without bound. `app_logs` stores every warning forever, including the warnings the dead embed path emits.
- Fix: retain mood and app logs for the same window as owner memory, or partition by month. Do not write a mood row per historical message.

### P2 — design drift, dead ends, missing tests

**P2.1 Instructions contradict the code**

- `.github/copilot-instructions.md` line 10 (2,000 tokens) and line 13 (llama3.2 / qwen2.5) vs `MAX_CONTEXT_TOKENS=30000` and a Qwen3-class card.
- `.github/instructions/python.instructions.md` still says to rename the tree to `src/raccbuddy/` and to keep Alembic under `core/db/migrations/`. The code uses `src/` and top-level `alembic/`. Contributors following that file will fight the repo.
- `README.md` lists `/help` as a command; `start` handler text was already corrected in 0.2.0, the README was not.
- `xai_embed_model` default is the placeholder `"v1"` (`config.py` lines 58–61).

**P2.2 Connector pattern is two patterns**

- Telegram is not a `BasePlugin`. WhatsApp is not a `BasePlugin` either; it is a Node process. `BasePlugin.handle_message` (`src/plugins/base.py` lines 31–34) has no history, cursor, or idempotency hook. `plugins/README.md` describes a third, slightly different, contract.
- Fix when adding providers: one ingest function, and a documented history method, rather than a third copy of `save_message`.

**P2.3 Integer primary keys**

- `messages.id` and `contacts.id` are `Integer` (`models.py` lines 53 and 67). A personal archive will not hit 2^31 quickly, but a multi-year multi-platform import plus retries (before P0.2) moves in that direction for no benefit. New id columns should be `BigInteger`. Not worth a rewrite of existing FKs by itself.

**P2.4 Group chats are flagged and then ignored**

- The API accepts `is_group` and `group_name` (`api.py` lines 48–49) and never stores them. The WhatsApp bridge sends them (`index.js` lines 154–155). Group traffic will be ingested as a 1:1 contact named after the sender, which corrupts relationship scores if groups are forwarded.
- Fix: persist `is_group` and skip groups in scoring until there is a real group model.

**P2.5 Tests never exercise the bug**

- API tests cover the auth header only (`tests/test_api_auth.py`). No test posts a message, replays it, or checks order. No test opens a WhatsApp history page. `tests/test_chat_handler.py` asserts `save_message` was called, not that a second delivery is a no-op. Summarizer has no tests. Memory tests mock the session; nothing checks that cosine search has an index or that `add_message` embedding is a no-op.
- Fix: in-memory ingest tests for replay, edit, and out-of-order timestamps, plus a catch-up test that a 100-message day does not call the LLM 100 times.

**P2.6 Voice and forwarded Telegram are the same live-only pipe**

- `src/handlers/voice.py` saves the transcript at ingest time with no `message_id`. Forwarded messages (`chat.py` `_extract_contact_id`, lines 217–222) store a single bubble, not the source chat. That matches the Bot API, and it should be documented so nobody expects `/start` to reconstruct a Telegram history.

**P2.7 `Message.id` autocommit per row**

- `save_message` opens a session and commits once per message (`crud.py` lines 40–42). A 10k backfill is 10k transactions and 10k round trips. The API then does a contact upsert in a second transaction (`api.py` lines 144–160).
- Fix: batch insert inside one transaction per page (a few hundred rows), still relying on the unique key.

## Suggested order of work

1. Idempotent ingest, stable chat key, original timestamps, cursors. Cover it with replay tests. This is the history bug.
2. WhatsApp `fetchMessages` backfill (with the local-cache limit documented) and a Telegram export importer (Bot API limit documented). Bounded day-summary catch-up. No per-message LLM on the historical path.
3. Only then change the default model to `qwen3:8b` or `qwen3:14b`, drop `MAX_CONTEXT_TOKENS` to a window that fits, and replace the reflector and sentiment parsers with structured output.
4. Re-embed with `qwen3-embedding:0.6b` at 768 (MRL) or plan a 1024 migration, then add HNSW.
5. New connectors go through the ingest API. Do not copy `example_echo.py`.

## Assumptions and things not verified

- VRAM and tokens/sec figures are from public estimator pages, not from a run on this RTX 5080. ModelFit and VRAM Guide disagree on whether Qwen3 14B Q4 can hold 32k context on 16 GB. The safe recommendation is 8k–16k until `nvidia-smi` says otherwise.
- Ollama tag names (`qwen3:14b`, `qwen3-embedding:0.6b`) matched the Ollama library pages checked in October 2026. Tags move. Confirm with `ollama pull` before changing defaults.
- MTEB numbers in the embedding table are from those same secondary writeups (a MorphLLM roundup and the Qwen3-Embedding model card). They were not re-benchmarked here.
- No GitHub issues were available in the local clone beyond the issue templates. History was three commits (`0ff708c`, `4bebe01`, `66b3d7c`). If "Hermes" referred to a branch or a private note that never landed, it is not in this tree.
- WhatsApp `fetchMessages` behavior is from whatsapp-web.js 1.26 as used in `whatsapp-service/package.json`: it loads up to `limit` messages already visible to the linked Web session, newest window first. It is not a complete server-side archive API. Very old messages can be missing until the phone has synced them.
- Telegram Bot API still has no method that returns prior chat history. That limit is from the Bot API itself, not from an experiment against a bot token in this environment.
