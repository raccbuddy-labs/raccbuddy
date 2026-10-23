/**
 * Forwards a WhatsApp message payload to the Python core REST API.
 *
 * Retries up to 3 times with exponential back-off on transient failures.
 */

const MAX_RETRIES = 3;
const BASE_DELAY_MS = 1000;

/**
 * POST the message payload to the Python core.
 *
 * @param {string} coreUrl  - Base URL of the Python core (e.g. http://localhost:8000)
 * @param {object} payload  - Message payload object
 */
function authHeaders() {
  const headers = { "Content-Type": "application/json" };
  if (process.env.API_SECRET_KEY) {
    headers["X-API-Key"] = process.env.API_SECRET_KEY;
  }
  return headers;
}

async function forwardMessage(coreUrl, payload) {
  const url = `${coreUrl}/api/messages`;
  console.log(`🚀 Forwarding message from ${payload.contact_name} to core...`);
  for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify(payload),
      });

      if (res.ok) {
        console.log(
          `📨 Forwarded message from ${payload.contact_name} (${payload.from_id})`
        );
        return;
      }

      // Non-retryable client errors
      if (res.status >= 400 && res.status < 500) {
        const body = await res.text();
        console.error(
          `❌ Core rejected message (${res.status}): ${body}`
        );
        return;
      }

      // Server error — retry
      console.warn(
        `⚠️  Core returned ${res.status}, retrying (${attempt}/${MAX_RETRIES})...`
      );
    } catch (err) {
      console.warn(
        `⚠️  Failed to reach core (attempt ${attempt}/${MAX_RETRIES}): ${err.message}`
      );
    }

    // Exponential back-off
    if (attempt < MAX_RETRIES) {
      await sleep(BASE_DELAY_MS * Math.pow(2, attempt - 1));
    }
  }

  console.error(
    `❌ Gave up forwarding message from ${payload.contact_name} after ${MAX_RETRIES} attempts`
  );
}

/**
 * POST a history page. The core dedupes by external_message_id.
 *
 * @param {string} coreUrl
 * @param {object[]} messages
 * @param {boolean} historyComplete
 */
async function forwardBatch(coreUrl, messages, historyComplete) {
  if (!messages.length) return { inserted: 0, duplicates: 0 };
  const url = `${coreUrl}/api/messages/batch`;
  const res = await fetch(url, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({
      messages,
      history_complete: Boolean(historyComplete),
    }),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Core rejected history batch (${res.status}): ${body}`);
  }
  return res.json();
}

/**
 * @param {string} coreUrl
 * @param {string} scope chat JID
 */
async function fetchCursor(coreUrl, scope) {
  const url = new URL(`${coreUrl}/api/sync/cursor`);
  url.searchParams.set("platform", "whatsapp");
  url.searchParams.set("scope", scope);
  const res = await fetch(url, { headers: authHeaders() });
  if (!res.ok) return null;
  return res.json();
}

/** Helper: sleep for a given duration (ms). */
function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

module.exports = { forwardMessage, forwardBatch, fetchCursor, sleep };
