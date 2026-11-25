// lib/poll.ts: 30 s refreshes that pause while hidden and back off on failure.
import { afterEach, expect, test, vi } from "vitest";

import { MAX_BACKOFF_MS, nextDelay, pollWhileVisible } from "../lib/poll.ts";

afterEach(() => vi.useRealTimers());

function page(state: "visible" | "hidden" = "visible") {
  const listeners: (() => void)[] = [];
  return {
    visibilityState: state,
    addEventListener: (_: string, fn: () => void) => listeners.push(fn),
    removeEventListener: () => {},
    show() { this.visibilityState = "visible"; listeners.forEach((fn) => fn()); },
  };
}

test("the wait doubles per failure up to five minutes", () => {
  expect([0, 1, 2, 3, 4].map((n) => nextDelay(30_000, n))).toEqual([30_000, 60_000, 120_000, 240_000, MAX_BACKOFF_MS]);
});

test("polls while visible, backs off after a failure, resets after a success", async () => {
  vi.useFakeTimers();
  const results = [false, true, true];
  const run = vi.fn(async () => { if (!results.shift()) throw new Error("down"); });
  const stop = pollWhileVisible(run, 30_000, page() as unknown as Document);
  await vi.advanceTimersByTimeAsync(30_000);
  expect(run).toHaveBeenCalledTimes(1);              // failed
  await vi.advanceTimersByTimeAsync(30_000);
  expect(run).toHaveBeenCalledTimes(1);              // waiting 60 s now
  await vi.advanceTimersByTimeAsync(30_000);
  expect(run).toHaveBeenCalledTimes(2);              // succeeded: back to 30 s
  await vi.advanceTimersByTimeAsync(30_000);
  expect(run).toHaveBeenCalledTimes(3);
  stop();
  await vi.advanceTimersByTimeAsync(120_000);
  expect(run).toHaveBeenCalledTimes(3);
});

test("a hidden tab does not poll until it is shown", async () => {
  vi.useFakeTimers();
  const run = vi.fn(async () => {});
  const doc = page("hidden");
  const stop = pollWhileVisible(run, 30_000, doc as unknown as Document);
  await vi.advanceTimersByTimeAsync(300_000);
  expect(run).not.toHaveBeenCalled();
  doc.show();
  await vi.advanceTimersByTimeAsync(0);
  expect(run).toHaveBeenCalledTimes(1);
  stop();
});
