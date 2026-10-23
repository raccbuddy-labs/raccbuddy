const test = require("node:test");
const assert = require("node:assert/strict");

const { historyDecision } = require("../src/history");

const cursor = {
  newest_external_id: "old",
  newest_at: "2026-06-01T00:00:00.000Z",
  backfill_complete: false,
};

test("a short page that misses the cursor is a gap and should expand", () => {
  const page = [
    {
      external_message_id: "n1",
      timestamp: "2026-06-11T00:00:00.000Z",
      text: "x",
    },
  ];
  const decision = historyDecision(page, cursor, 1, 100, 500);
  assert.equal(decision.reached, false);
  assert.equal(decision.complete, false);
  assert.equal(decision.expand, true);
});

test("a short page that overlaps the cursor is complete", () => {
  const page = [
    {
      external_message_id: "old",
      timestamp: "2026-06-01T00:00:00.000Z",
      text: "kept",
    },
    {
      external_message_id: "n1",
      timestamp: "2026-06-02T00:00:00.000Z",
      text: "new",
    },
  ];
  const decision = historyDecision(page, cursor, 2, 100, 500);
  assert.equal(decision.reached, true);
  assert.equal(decision.complete, true);
  assert.equal(decision.expand, false);
});

test("the first backfill has no cursor and a short page is complete", () => {
  const page = [
    {
      external_message_id: "m1",
      timestamp: "2024-01-01T00:00:00.000Z",
      text: "hi",
    },
  ];
  const decision = historyDecision(page, null, 1, 500, 500);
  assert.equal(decision.complete, true);
  assert.equal(decision.expand, false);
});

test("a full page at the cap that still misses the cursor stays incomplete", () => {
  const page = [
    {
      external_message_id: "n1",
      timestamp: "2026-06-11T00:00:00.000Z",
      text: "x",
    },
  ];
  const decision = historyDecision(page, cursor, 500, 500, 500);
  assert.equal(decision.reached, false);
  assert.equal(decision.complete, false);
  assert.equal(decision.expand, false);
});
