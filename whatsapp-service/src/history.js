/**
 * WhatsApp history backfill.
 *
 * whatsapp-web.js only emits `message_create` for messages that arrive
 * while the client is connected. `chat.fetchMessages({ limit })` loads up
 * to `limit` messages from the linked Web session, newest window first.
 * Messages older than that window are not available until WhatsApp Web has
 * synced them, and there is no server-side "entire archive" API.
 *
 * The Python core dedupes by `external_message_id`, so posting the same
 * page again inserts nothing. The cursor tells the next run whether to
 * pull the large backfill window or only a recent incremental page.
 */

const DEFAULT_MAX = 500;
const DEFAULT_PAGE = 100;

function historyLimits() {
  const maxPerChat = positiveInt(
    process.env.WHATSAPP_HISTORY_MAX_PER_CHAT,
    DEFAULT_MAX
  );
  const pageSize = positiveInt(process.env.WHATSAPP_HISTORY_PAGE_SIZE, DEFAULT_PAGE);
  return {
    maxPerChat,
    pageSize: Math.min(pageSize, maxPerChat),
  };
}

function positiveInt(value, fallback) {
  const parsed = parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

/**
 * How many messages to ask WhatsApp for on this pass.
 *
 * No cursor, or a cursor whose backfill is still incomplete: the full cap,
 * so raising WHATSAPP_HISTORY_MAX_PER_CHAT on a later restart can reach
 * further back. A completed backfill only needs the recent page.
 */
function fetchLimit(cursor, limits) {
  if (!cursor || !cursor.backfill_complete) {
    return limits.maxPerChat;
  }
  return limits.pageSize;
}

function messagePayload(msg, chat) {
  const chatJid = (chat.id && chat.id._serialized) || msg.from;
  const externalId =
    (msg.id && (msg.id._serialized || msg.id.id)) || "";
  if (!externalId) return null;
  return {
    platform: "whatsapp",
    chat_id: chatJid,
    external_message_id: String(externalId),
    from_me: Boolean(msg.fromMe),
    text: msg.body || "",
    timestamp: new Date(msg.timestamp * 1000).toISOString(),
    is_group: Boolean(chat.isGroup),
    group_name: chat.isGroup ? chat.name : null,
  };
}

/**
 * Walk chats and POST each page. `deps` is injected so unit tests are not
 * required here; the Python suite covers the dedup contract this relies on.
 */
async function syncHistory(client, deps) {
  if (process.env.WHATSAPP_HISTORY_ENABLED === "false") {
    console.log("WhatsApp history sync disabled");
    return;
  }
  const limits = historyLimits();
  const chats = await client.getChats();
  for (const chat of chats) {
    const scope = chat.id && chat.id._serialized;
    if (!scope || scope === "status@broadcast") continue;
    try {
      await syncChat(client, chat, scope, limits, deps);
    } catch (err) {
      console.error(`History sync failed for ${scope}: ${err.message}`);
    }
    await sleep(deps.delayMs || 750);
  }
}

async function syncChat(client, chat, scope, limits, deps) {
  const cursor = await deps.fetchCursor(scope);
  let limit = fetchLimit(cursor, limits);
  // One expansion pass when the page does not overlap the cursor.
  for (let attempt = 0; attempt < 2; attempt++) {
    const raw = await chat.fetchMessages({ limit });
    const payloads = [];
    for (const msg of raw) {
      const base = messagePayload(msg, chat);
      if (base) payloads.push(base);
    }
    const decision = historyDecision(
      payloads,
      cursor,
      raw.length,
      limit,
      limits.maxPerChat
    );
    const messages = [];
    for (const msg of raw) {
      const base = messagePayload(msg, chat);
      if (!base || !base.text) continue;
      const contact = await describeContact(msg, chat);
      messages.push({
        ...base,
        from_id: contact.number,
        contact_name: contact.name,
      });
    }
    await deps.forwardBatch(messages, { historyComplete: decision.complete });
    if (!decision.expand) return;
    limit = limits.maxPerChat;
  }
}

/**
 * True when this page overlaps the stored watermark.
 *
 * A page of only-newer messages has a hole between the cursor and the
 * oldest row. An empty payload has nothing to miss.
 */
function pageReachesCursor(messages, cursor) {
  if (!cursor || !cursor.newest_at) return true;
  const watermark = Date.parse(cursor.newest_at);
  if (!Number.isFinite(watermark)) return true;
  if (!messages.length) return true;
  const known = cursor.newest_external_id;
  for (const msg of messages) {
    if (known && msg.external_message_id === known) return true;
    const ts = Date.parse(msg.timestamp);
    if (!Number.isNaN(ts) && ts <= watermark) return true;
  }
  return false;
}

/**
 * Decide whether this fetch finished history and whether to ask again
 * with a larger limit. A short page that misses the cursor is not complete.
 */
function historyDecision(messages, cursor, rawCount, limit, maxPerChat) {
  const exhausted = rawCount < limit;
  const reached = pageReachesCursor(messages, cursor);
  const complete = exhausted && reached;
  const expand =
    Boolean(cursor && cursor.newest_at) && !reached && limit < maxPerChat;
  return { complete, expand, reached };
}

async function describeContact(msg, chat) {
  try {
    if (msg.fromMe) {
      const contact = await chat.getContact();
      return {
        name: contact.pushname || contact.name || contact.number || "Unknown",
        number: contact.number || (chat.id && chat.id._serialized) || "",
      };
    }
    const contact = await msg.getContact();
    return {
      name: contact.pushname || contact.name || contact.number || "Unknown",
      number: contact.number || msg.from,
    };
  } catch {
    return {
      name: chat.name || "Unknown",
      number: (chat.id && chat.id._serialized) || msg.from || "",
    };
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

module.exports = {
  fetchLimit,
  historyDecision,
  historyLimits,
  messagePayload,
  pageReachesCursor,
  syncHistory,
};
