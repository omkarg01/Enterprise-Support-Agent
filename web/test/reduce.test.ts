import { expect, test } from "vitest";

import { addUserMessage, initialState, reduce, type ChatState } from "../lib/reduce.ts";

type Event = { event: string; data: Record<string, any>; at?: number };
const run = (events: Event[], state: ChatState = initialState()) => events.reduce(reduce, state);

test("status shows while the reply is held, then the checked reply replaces it", () => {
  let state = addUserMessage(initialState(), "My invoice is wrong");
  state = reduce(state, { event: "status", data: { state: "queued", text: "Message received" } });
  state = reduce(state, { event: "status", data: { text: "Checking invoice history" } });
  expect(state.status).toBe("Checking invoice history");
  expect(state.messages).toHaveLength(1);
  state = reduce(state, { event: "final", data: { text: "Your invoice was corrected.", turn_id: "t-1" }, at: 5 });
  expect(state.status).toBeNull();
  expect(state.messages.at(-1)).toEqual({
    role: "assistant", text: "Your invoice was corrected.", at: 5, turnId: "t-1", citations: [], actions: [],
  });
});

test("citations, with their excerpt, attach to the reply that follows them", () => {
  const state = run([
    { event: "citation", data: { doc_id: "kb-12", title: "Refund policy", excerpt: "Within 30 days." } },
    { event: "final", data: { text: "Refunds take 5 days.", actions: [{ id: "talk_to_human", label: "Talk to a human" }] } },
  ]);
  expect(state.messages[0].citations).toEqual([{ doc_id: "kb-12", title: "Refund policy", excerpt: "Within 30 days." }]);
  expect(state.messages[0].actions?.[0].id).toBe("talk_to_human");
});

test("an expired stream clears the view before the history is replayed, which keeps its trace and sources", () => {
  const cited = { doc_id: "kb-12", title: "Refund policy" };
  const state = run([
    { event: "final", data: { text: "stale" } },
    { event: "status", data: { code: "stream_expired" } },
    { event: "final", data: { history: true, role: "user", content: "Hi", at: "2026-11-01T10:00:00Z" } },
    { event: "final", data: { history: true, role: "assistant", text: "Hello", turn_id: "old", citations: [cited] } },
    { event: "final", data: { history: true, role: "assistant", text: "Older reply" } }, // stored before 2026-11-19
  ]);
  expect(state.messages.map((m) => [m.role, m.text, m.turnId, m.citations])).toEqual([
    ["user", "Hi", undefined, []], ["assistant", "Hello", "old", [cited]], ["assistant", "Older reply", undefined, []]]);
  expect(state.status).toBeNull();
  expect(state.messages[0].at).toBe(Date.parse("2026-11-01T10:00:00Z")); // a reopened chat keeps its times
});

test("errors and hand-offs end the status line", () => {
  expect(run([{ event: "status", data: { text: "x" } }, { event: "error", data: {} }]).status).toBeNull();
  const handed = run([{ event: "handoff", data: { text: "Alex from support joined" } }]);
  expect(handed.messages[0].text).toBe("Alex from support joined");
});

test("unknown event types change nothing", () => {
  const state = initialState();
  expect(reduce(state, { event: "token", data: {} })).toBe(state);
});
