/**
 * RaccBuddy WhatsApp Bridge
 *
 * Connects to WhatsApp via whatsapp-web.js, listens for incoming messages,
 * and forwards them to the Python core REST API at /api/messages.
 *
 * Session is persisted locally via LocalAuth (no re-scan after first QR).
 */

require("dotenv").config();

const { Client, LocalAuth } = require("whatsapp-web.js");
const qrcode = require("qrcode-terminal");
const express = require("express");
const fs = require("fs");
const path = require("path");
const { forwardMessage, forwardBatch, fetchCursor } = require("./forwarder");
const { syncHistory, messagePayload } = require("./history");

// ---------------------------------------------------------------------------
// Clean up stale Chromium lock files left after crash / forced stop
// ---------------------------------------------------------------------------

function cleanStaleLocks() {
  const authDir = path.join(__dirname, "..", ".wwebjs_auth");
  if (!fs.existsSync(authDir)) return;

  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(full);
      } else if (entry.name === "SingletonLock" || entry.name === "SingletonCookie") {
        fs.unlinkSync(full);
        console.log(`🧹 Removed stale lock: ${full}`);
      }
    }
  };
  walk(authDir);
}

cleanStaleLocks();

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const PORT = parseInt(process.env.PORT, 10) || 3001;
const PYTHON_CORE_URL =
  process.env.PYTHON_CORE_URL || "http://localhost:8000";

// ---------------------------------------------------------------------------
// WhatsApp client (persistent session via LocalAuth)
// ---------------------------------------------------------------------------

const client = new Client({
  authStrategy: new LocalAuth(),
  puppeteer: {
    headless: true,
    args: [
      "--no-sandbox",
      "--disable-setuid-sandbox",
      "--disable-dev-shm-usage",
      "--disable-gpu",
      "--disable-features=LockProfileCookieDatabase",
    ],
  },
});

// ---------------------------------------------------------------------------
// QR code — displayed in terminal for first-time auth
// ---------------------------------------------------------------------------

client.on("qr", (qr) => {
  console.log("\n📱 Scan this QR code with WhatsApp:\n");
  qrcode.generate(qr, { small: true });
});

// ---------------------------------------------------------------------------
// Ready — session authenticated
// ---------------------------------------------------------------------------

client.on("ready", () => {
  console.log("✅ WhatsApp client is ready and connected!");
  // History is not delivered by message_create. Fetch the local window,
  // then keep listening for live messages. Dedup lives in the Python core.
  syncHistory(client, {
    delayMs: 750,
    fetchCursor: (scope) => fetchCursor(PYTHON_CORE_URL, scope),
    forwardBatch: (messages, opts) =>
      forwardBatch(PYTHON_CORE_URL, messages, opts && opts.historyComplete),
  }).catch((err) => {
    console.error("History sync failed:", err.message);
  });
});

// ---------------------------------------------------------------------------
// Authentication events
// ---------------------------------------------------------------------------

client.on("authenticated", () => {
  console.log("🔐 Session authenticated — no QR needed next time.");
});

client.on("auth_failure", (msg) => {
  console.error("❌ Authentication failed:", msg);
});

client.on("disconnected", (reason) => {
  console.warn("⚠️  WhatsApp disconnected:", reason);
});

// ---------------------------------------------------------------------------
// Message handler — forward every incoming message to the Python core
// ---------------------------------------------------------------------------

client.on("message_create", async (msg) => {
  try {
    // Skip status broadcasts
    if (msg.from === "status@broadcast") return;

    // Determine if this is a group message
    const chat = await msg.getChat();
    const isGroup = chat.isGroup;

    // Use the chat's JID as the stable conversation identifier.
    // For 1:1 chats this is the contact's JID regardless of message direction.
    const chatJid = chat.id._serialized || msg.from;

    // Determine contact info (the other person in the conversation)
    let contactName;
    let contactNumber;

    if (msg.fromMe) {
      // Outgoing message — the contact is whoever we're chatting with
      try {
        const chatContact = await chat.getContact();
        contactName =
          chatContact.pushname || chatContact.name || chatContact.number || "Unknown";
        contactNumber = chatContact.number || chatJid;
      } catch {
        contactName = chat.name || "Unknown";
        contactNumber = chatJid;
      }
    } else {
      // Incoming message — the contact is the sender
      const senderContact = await msg.getContact();
      contactName =
        senderContact.pushname || senderContact.name || senderContact.number || "Unknown";
      contactNumber = senderContact.number || msg.from;
    }

    console.log(
      `📩 ${msg.fromMe ? "Sent" : "Received"} message ${msg.fromMe ? "to" : "from"} ${contactName}: ${msg.body.substring(0, 50)}...`
    );

    // Build payload matching the Python core schema. external_message_id
    // is the idempotency key: live delivery and history sync share it.
    const base = messagePayload(msg, chat) || {};
    const payload = {
      platform: "whatsapp",
      chat_id: chatJid,
      external_message_id: base.external_message_id || null,
      from_id: contactNumber,
      contact_name: contactName,
      text: msg.body,
      timestamp: new Date(msg.timestamp * 1000).toISOString(),
      is_group: isGroup,
      group_name: isGroup ? chat.name : null,
      from_me: msg.fromMe,  // Flag if message is from owner
    };

    await forwardMessage(PYTHON_CORE_URL, payload);
  } catch (err) {
    console.error("Error processing message:", err.message);
  }
});

// Edits keep the same provider id. The core updates the row in place.
client.on("message_edit", async (msg) => {
  try {
    if (!msg || msg.from === "status@broadcast") return;
    const chat = await msg.getChat();
    const base = messagePayload(msg, chat);
    if (!base || !base.external_message_id) return;
    let contactName = chat.name || "Unknown";
    let contactNumber = base.chat_id;
    try {
      const contact = msg.fromMe ? await chat.getContact() : await msg.getContact();
      contactName = contact.pushname || contact.name || contact.number || contactName;
      contactNumber = contact.number || contactNumber;
    } catch {
      // Fall back to the chat JID. The id is what dedup uses.
    }
    await forwardMessage(PYTHON_CORE_URL, {
      ...base,
      from_id: contactNumber,
      contact_name: contactName,
      edited_at: new Date().toISOString(),
    });
  } catch (err) {
    console.error("Error processing edit:", err.message);
  }
});

// ---------------------------------------------------------------------------
// Express health-check server
// ---------------------------------------------------------------------------

const app = express();
app.use(express.json());

/** Health-check endpoint */
app.get("/health", (_req, res) => {
  const state = client.info ? "connected" : "disconnected";
  res.json({ status: "ok", whatsapp: state });
});

/** Graceful shutdown */
const shutdown = async () => {
  console.log("\n🛑 Shutting down WhatsApp service...");
  await client.destroy();
  process.exit(0);
};
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

app.listen(PORT, () => {
  console.log(`🦝 RaccBuddy WhatsApp service listening on port ${PORT}`);
  console.log(`   Python core URL: ${PYTHON_CORE_URL}`);
});

client.initialize();
