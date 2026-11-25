// The Services tab in a real browser with /v1 stubbed (tools.services): Services sits above Chat in the rail;
// an offer card's Stop server posts the confirmation, the changed service's card arrives on the stream and
// opens Services, which shows the server stopping and the change in its activity. Under the production CSP.
import { expect, test, type Page } from "@playwright/test";

const token = (tier: string) =>
  `h.${Buffer.from(JSON.stringify({ sub: "sam@acme.test", tid: "t_us", tier, exp: 4e9 })).toString("base64url")}.s`;
const sse = (events: [string, object][], from: number) =>
  events.map(([event, data], i) => `id: ${from + i}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join("");

const SERVER = { service: "payments-api-01", service_kind: "server", region: "eu-central-1" };
const OFFER = sse([
  ["action_card", { turn_id: "turn-1", kind: "service_action_proposal", title: "Stop payments-api-01?",
    action_id: "ACT-00003", action: "stop", replicas: null, status: "running", ...SERVER,
    actions: [{ type: "confirm_action", proposal: "ACT-00003", label: "Stop server" }] }],
  ["final", { turn_id: "turn-1", role: "assistant", text: "I can stop the server payments-api-01 (eu-central-1, "
    + "running now). Nothing has changed yet: use Stop server to confirm, and you can follow it under Services." }],
], 1);
const DONE = sse([
  ["action_card", { turn_id: "turn-2", kind: "service", title: "payments-api-01 is stopping", action: "stop",
    status: "stopping", replicas: null, ...SERVER }],
  ["final", { turn_id: "turn-2", role: "assistant",
    text: "payments-api-01 is stopping now and will be stopped in a few seconds. You can follow it under Services." }],
], 3);

const service = (n: number, name: string, kind: string, status: string, replicas: number | null = null) =>
  ({ service_id: `svc-sam-${n}`, name, kind, region: "eu-central-1", status, replicas, updated_at: "2026-11-17T10:00:00Z" });

async function stub(page: Page) {
  const state = { offered: false, confirmed: false, done: false };
  await page.route("**/v1/**", async (route) => {
    const url = route.request().url();
    if (url.endsWith("/identity/anonymous")) return route.fulfill({ json: { access_token: token("T1_VERIFIED") } });
    if (url.endsWith("/events")) {
      let body = "";
      if (!state.offered) { body = OFFER; state.offered = true; }
      else if (state.confirmed && !state.done) { body = DONE; state.done = true; }
      return route.fulfill({ body, contentType: "text/event-stream" });
    }
    if (url.endsWith("/actions/ACT-00003/confirm")) {
      state.confirmed = true;
      return route.fulfill({ status: 202, json: { turn_id: "turn-2", status: "QUEUED" } });
    }
    if (url.endsWith("/v1/services")) {
      return route.fulfill({ json: {
        services: [service(2, "batch-worker-02", "server", "running"),
          service(1, "payments-api-01", "server", state.confirmed ? "stopping" : "running"),
          service(3, "staging-web-01", "server", "stopped"),
          service(4, "checkout-worker", "app", "running", 2)],
        activity: state.confirmed ? [{ action_id: "ACT-00003", service: "payments-api-01", action: "stop",
          replicas: null, conversation_id: "c1", at: new Date().toISOString() }] : [],
      } });
    }
    return route.fulfill({ status: 202, json: { turn_id: "turn-9", status: "QUEUED" } });
  });
}

const shot = (page: Page, name: string) => process.env.SHOTS
  ? page.screenshot({ path: `${process.env.SHOTS}/${name}.png` }) : Promise.resolve();

test("stop a server from the chat and watch it stop under Services", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (message) => message.type() === "error" && errors.push(message.text()));
  page.on("pageerror", (error) => errors.push(error.message));
  await page.setViewportSize({ width: 1440, height: 900 });
  await stub(page);
  await page.goto("/");

  const rail = page.getByRole("navigation", { name: "Portal" }).getByRole("listitem");
  await expect(rail.nth(0)).toHaveText("Services");
  await expect(rail.nth(1)).toHaveText("New conversation");

  await expect(page.getByText("Stop payments-api-01?")).toBeVisible();
  await expect(page.getByText("Nothing changes until you confirm.")).toBeVisible();
  await shot(page, "service-offer");

  const confirm = page.waitForRequest((r) => r.method() === "POST" && r.url().endsWith("/actions/ACT-00003/confirm"));
  await page.getByRole("button", { name: "Stop server" }).click();
  expect((await confirm).headers()["idempotency-key"]).toBeTruthy();
  await expect(page.getByText("payments-api-01 is stopping", { exact: true })).toBeVisible({ timeout: 10_000 });
  await shot(page, "service-done");

  await page.getByRole("link", { name: "Open Services" }).click();
  await expect(page).toHaveURL(/\/t\/t_us\/services$/);
  const servers = page.getByRole("list", { name: "Servers" });
  await expect(servers.getByRole("listitem")).toHaveCount(3);
  await expect(servers.getByRole("listitem").filter({ hasText: "payments-api-01" })).toContainText("Stopping");
  await expect(servers.getByRole("listitem").filter({ hasText: "payments-api-01" })).toContainText("ACT-00003");
  await expect(page.getByRole("list", { name: "Container apps" })).toContainText("2 replicas");
  await expect(page.getByRole("list", { name: "Activity" })).toContainText("Stopped payments-api-01");
  await shot(page, "services-tab");
  expect(errors).toEqual([]); // includes Content-Security-Policy violations
});

test("a guest sees Services ask them to sign in", async ({ page }) => {
  await page.route("**/v1/**", (route) => route.request().url().endsWith("/identity/anonymous")
    ? route.fulfill({ json: { access_token: token("T0_ANON") } })
    : route.fulfill({ body: "", contentType: "text/event-stream" }));
  await page.goto("/t/t_us/services");
  await expect(page.getByText("Sign in to see your services")).toBeVisible();
});
