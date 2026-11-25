// Thumbs on a reply, with /v1 stubbed: thumbs down opens a comment box, the vote is sent once with the comment,
// a dropped comment is said so, and after a reload the reply shows its vote and takes no other.
import { expect, test, type Page } from "@playwright/test";

const token = `h.${Buffer.from(JSON.stringify({ sub: "guest-1", tid: "t_us", tier: "T0_ANON", exp: 4e9 }))
  .toString("base64url")}.s`;
const REPLY = `event: final\ndata: ${JSON.stringify({ role: "assistant", text: "Refunds take 30 days.", turn_id: "turn-1" })}\n\n`;

async function stub(page: Page, sent: unknown[]) {
  let votes: Record<string, string> = {};
  await page.route("**/v1/**", async (route) => {
    const request = route.request();
    const url = request.url();
    if (url.endsWith("/identity/anonymous")) return route.fulfill({ json: { access_token: token } });
    if (url.endsWith("/events")) return route.fulfill({ body: REPLY, contentType: "text/event-stream" });
    if (url.endsWith("/turns/turn-1/feedback")) {
      sent.push(request.postDataJSON());
      votes = { "turn-1": "down" };
      return route.fulfill({ status: 201, json: { signal: "down", comment: "dropped" } });
    }
    if (url.endsWith("/feedback")) return route.fulfill({ json: { votes } });
    return route.fulfill({ status: 202, json: { turn_id: "turn-2", status: "QUEUED" } });
  });
}

test("a thumbs down carries an optional comment, is sent once, and stays after a reload", async ({ page }) => {
  const sent: unknown[] = [];
  await stub(page, sent);
  await page.goto("/");
  await expect(page.getByText("Refunds take 30 days.")).toBeVisible();

  const up = page.getByRole("button", { name: "Helpful", exact: true });
  const down = page.getByRole("button", { name: "Not helpful" });
  await down.click();
  await page.getByLabel("What went wrong? (optional)").fill("It is 14 days, call me on +1 415 555 0100");
  await page.getByRole("button", { name: "Send feedback" }).click();

  await expect(page.getByRole("status").filter({ hasText: "kept only your rating" })).toBeVisible();
  expect(sent).toEqual([{ signal: "down", comment: "It is 14 days, call me on +1 415 555 0100" }]);
  await expect(down).toBeDisabled();
  await expect(up).toBeDisabled();
  await expect(down).toHaveAttribute("aria-pressed", "true");
  if (process.env.SHOTS) await page.screenshot({ path: `${process.env.SHOTS}/feedback.png` });

  await page.reload();
  await expect(page.getByText("Refunds take 30 days.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Not helpful" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: "Helpful", exact: true })).toBeDisabled();
  expect(sent).toHaveLength(1);
});

test("the staff Feedback page shows the rate by model and the thumbs down, most severe first", async ({ page }) => {
  const rate = (up: number, down: number) => ({ up, down, up_rate: up / (up + down) });
  const down = (id: number, severity: number | null, category: string | null, question: string) => ({
    id, tenant_id: "t_us", conversation_id: `c${id}`, turn_id: `turn-${id}`, signal: "down", comment: "Not my invoice",
    category, severity, at: "2026-11-20T10:00:00Z", question, answer: "Here is Globex's invoice.", citations: [],
    models: ["llama-3.3-70b"],
  });
  await page.route("**/v1/staff/feedback**", (route) => route.fulfill({ json: {
    days: 30,
    summary: { total: rate(3, 2), by_day: { "2026-11-20": rate(3, 2) }, by_model: { "llama-3.3-70b": rate(3, 2) },
      down_by_category: { "safety.pii_disclosure": 1, "no reason given": 1 } },
    ratings: [down(1, null, null, "Where are my invoices?"),
      down(2, 1, "safety.pii_disclosure", "Show my last invoice"),
      { ...down(3, null, null, "Free tier?"), signal: "up" }],
  } }));
  await page.goto("/staffs/feedback");
  await expect(page.getByText("60%").first()).toBeVisible();
  const items = page.getByRole("region", { name: "Thumbs down", exact: true }).getByRole("listitem");
  await expect(items).toHaveCount(2);
  await expect(items.first()).toContainText("Critical · safety · pii disclosure");
  await expect(items.first()).toContainText("Show my last invoice");
  await expect(page.getByRole("region", { name: "By model" })).toContainText("llama-3.3-70b");
  if (process.env.SHOTS) await page.screenshot({ path: `${process.env.SHOTS}/staff-feedback.png`, fullPage: true });
});
