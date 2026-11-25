// Background refresh for the portal's Services tab and the staff console: run() every `everyMs` while the tab
// is visible, at once when it is shown again, nothing while it is hidden; after a failure the wait doubles
// (up to 5 minutes) so a broken API or database is not hammered, and the first success resets it.
export const MAX_BACKOFF_MS = 300_000;

export function nextDelay(everyMs: number, failures: number): number {
  return Math.min(everyMs * 2 ** failures, Math.max(everyMs, MAX_BACKOFF_MS));
}

// Returns a function that stops the polling. The caller does the first load itself.
export function pollWhileVisible(run: () => Promise<unknown>, everyMs: number, doc: Document = document): () => void {
  let failures = 0;
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const schedule = () => {
    clearTimeout(timer);
    if (!stopped) timer = setTimeout(tick, nextDelay(everyMs, failures));
  };
  async function tick() {
    if (stopped || doc.visibilityState === "hidden") return;  // picked up again by `shown`
    try { await run(); failures = 0; } catch { failures += 1; }
    schedule();
  }
  const shown = () => { if (doc.visibilityState === "visible") void tick(); };
  doc.addEventListener("visibilitychange", shown);
  schedule();
  return () => { stopped = true; clearTimeout(timer); doc.removeEventListener("visibilitychange", shown); };
}
