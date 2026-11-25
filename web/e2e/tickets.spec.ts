// Support tickets in a real browser with /v1 stubbed (FR-47, AC-16): the proposal card's Raise ticket posts
// the confirmation, the raised ticket's card arrives on the stream and opens the ticket's page with its
// history, My tickets lists it; a guest's card leads to the email sign-in. Under the production CSP.
import { expect, test, type Page } from "@playwright/test";

const token = (tier: string) =>
  `h.${Buffer.from(JSON.stringify({ sub: "sam@acme.test", tid: "t_us", tier, exp: 4e9 })).toString("base64url")}.s`;

const TICKET = {
  ticket_id: "TKT-00007", category: "refund", subject: "Refund request: inv-sam-1", invoice_id: "inv-sam-1",
  amount_cents: 50000, currency: "USD", queue: "queue_billing", created_at: "2026-11-17T10:00:00+00:00",
};
const sse = (events: [string, object][], from: number) =>
  events.map(([event, data], i) => `id: ${from + i}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join("");
const OFFER = sse([
  ["action_card", { turn_id: "turn-1", kind: "ticket_proposal", title: "Raise ticket TKT-00007?", status: "proposed",
    ...TICKET, actions: [{ type: "confirm_ticket", proposal: "TKT-00007", label: "Raise ticket" }] }],
  ["final", { turn_id: "turn-1", role: "assistant", text: "I can raise a refund ticket for USD 500.00 on invoice "
    + "inv-sam-1 with our billing team. Nothing has been raised or changed yet: use Raise ticket to confirm." }],
], 1);
const RAISED = sse([
  ["action_card", { turn_id: "turn-2", kind: "ticket", title: "Ticket TKT-00007 raised", status: "open", ...TICKET }],
  ["final", { turn_id: "turn-2", role: "assistant",
    text: "Your ticket TKT-00007 is raised with our billing team. They will follow up on it." }],
], 3);
const SIGN_IN = sse([
  ["action_card", { turn_id: "turn-1", kind: "sign_in", title: "Sign in to raise a ticket",
    actions: [{ type: "sign_in", label: "Sign in with your email" }] }],
  ["final", { turn_id: "turn-1", role: "assistant", text: "Please sign in with your email code first." }],
], 1);
const DETAIL = {
  ...TICKET, status: "open", description: "I was charged twice on inv-sam-1", reason: "billing request",
  conversation_id: "c1", opened_at: "2026-11-17T10:01:00+00:00", updated_at: "2026-11-17T10:05:00+00:00",
  events: [
    { kind: "proposed", actor: "agent", text: "Refund request: inv-sam-1", at: "2026-11-17T10:00:00+00:00" },
    { kind: "confirmed", actor: "sam@acme.test", text: "", at: "2026-11-17T10:01:00+00:00" },
    { kind: "note", actor: "customer", text: "It happened again this month", at: "2026-11-17T10:05:00+00:00" },
  ],
};

async function stub(page: Page, tier: string, first: string) {
  const state = { confirmed: false, offered: false, raised: false };
  await page.route("**/v1/**", async (route) => {
    const url = route.request().url();
    if (url.endsWith("/identity/anonymous")) return route.fulfill({ json: { access_token: token(tier) } });
    if (url.endsWith("/events")) {
      let body = "";
      if (!state.offered) { body = first; state.offered = true; }
      else if (state.confirmed && !state.raised) { body = RAISED; state.raised = true; }
      return route.fulfill({ body, contentType: "text/event-stream" });
    }
    if (url.endsWith("/tickets/TKT-00007/confirm")) {
      state.confirmed = true;
      return route.fulfill({ status: 202, json: { turn_id: "turn-2", status: "QUEUED" } });
    }
    if (url.endsWith("/v1/tickets")) return route.fulfill({ json: { tickets: [{ ...TICKET, status: "open" }] } });
    if (url.endsWith("/v1/tickets/TKT-00007")) return route.fulfill({ json: DETAIL });
    return route.fulfill({ status: 202, json: { turn_id: "turn-9", status: "QUEUED" } });
  });
}

function watchErrors(page: Page) {
  const errors: string[] = [];
  page.on("console", (message) => message.type() === "error" && errors.push(message.text()));
  page.on("pageerror", (error) => errors.push(error.message));
  return errors;
}

const shot = (page: Page, name: string) => process.env.SHOTS
  ? page.screenshot({ path: `${process.env.SHOTS}/${name}.png` }) : Promise.resolve();

test("raise a proposed ticket from the chat, open it, and find it under My tickets", async ({ page }) => {
  const errors = watchErrors(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await stub(page, "T1_VERIFIED", OFFER);
  await page.goto("/");

  await expect(page.getByText("Raise ticket TKT-00007?")).toBeVisible();
  await expect(page.getByText("$500.00")).toBeVisible();
  await expect(page.getByText("Billing team", { exact: true })).toBeVisible();
  await expect(page.getByText("Not raised yet")).toBeVisible();
  await shot(page, "ticket-proposal");

  const confirm = page.waitForRequest((r) => r.method() === "POST" && r.url().endsWith("/tickets/TKT-00007/confirm"));
  await page.getByRole("button", { name: "Raise ticket" }).click();
  expect((await confirm).headers()["idempotency-key"]).toBeTruthy();
  await expect(page.getByRole("button", { name: "Requested" })).toBeDisabled();

  const raised = page.getByRole("link", { name: "Open ticket TKT-00007" });
  await expect(raised).toBeVisible({ timeout: 10_000 });
  await shot(page, "ticket-raised");
  await raised.click();

  await expect(page).toHaveURL(/\/t\/t_us\/tickets\/TKT-00007$/);
  await expect(page.getByRole("heading", { name: "Ticket TKT-00007" })).toBeVisible();
  const history = page.getByRole("list", { name: "Ticket history" });
  await expect(history.getByText("Offered by the assistant")).toBeVisible();
  await expect(history.getByText("It happened again this month")).toBeVisible();
  await shot(page, "ticket-detail");

  await page.getByRole("link", { name: "My tickets" }).first().click();
  await expect(page).toHaveURL(/\/t\/t_us\/tickets$/);
  await expect(page.getByRole("list", { name: "My tickets" }).getByText("Refund request: inv-sam-1")).toBeVisible();
  await shot(page, "my-tickets");
  expect(errors).toEqual([]); // includes Content-Security-Policy violations
});

test("a guest's ticket card leads to the email sign-in, and My tickets asks them to sign in", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await stub(page, "T0_ANON", SIGN_IN);
  await page.goto("/");
  await page.getByRole("button", { name: "Sign in with your email" }).click();
  await expect(page.getByLabel("Work email")).toBeFocused();
  await shot(page, "sign-in");
  await page.keyboard.press("Escape"); // the sign-in pop-up is modal
  await page.getByRole("link", { name: "My tickets" }).click();
  await expect(page.getByText("Sign in to see your tickets")).toBeVisible();
});
