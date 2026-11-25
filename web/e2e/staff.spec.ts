// The demo staff console in a real browser with /v1/staff stubbed: the Overview counts, Tickets with a filter,
// one ticket's internal note, reply and refund approval (posting credit), Customers, a service stopped from
// Services, and the empty Hand-offs queue. Under the production Content-Security-Policy. Signed in already: a
// staff token in sessionStorage (the API, stubbed here, is what checks it).
import { expect, test, type Page } from "@playwright/test";

const at = "2026-11-18T10:00:00Z";
const ticket = (n: number, patch: object) => ({
  ticket_id: `TKT-0000${n}`, status: "open", category: "refund", subject: "Charged twice", invoice_id: "inv-sam-1",
  amount_cents: 50000, currency: "USD", queue: "queue_billing", created_at: at, tenant_id: "t_us",
  principal_id: "sam@acme.test", description: "Charged twice on <EMAIL_1a2b>", reason: "refund request",
  conversation_id: "c1", opened_at: at, updated_at: at, ...patch,
});

async function stub(page: Page) {
  const posts: { url: string; body: any }[] = [];
  const events: object[] = [{ kind: "proposed", actor: "agent", text: "Charged twice", at, visibility: "customer" },
    { kind: "confirmed", actor: "sam@acme.test", text: "", at, visibility: "customer" }];
  const refund = { ...ticket(7, {}) };
  const vpn = ticket(8, { status: "in_review", category: "technical", subject: "VPN drops", invoice_id: null,
    amount_cents: null, currency: null, queue: "queue_technical", tenant_id: "t_eu", principal_id: "greta@globex.test" });
  let worker = "running";
  const services = () => [
    { service_id: "svc-sam-2", name: "batch-worker-02", kind: "server", region: "eu-central-1", status: worker,
      replicas: null, updated_at: at, tenant_id: "t_us", principal_id: "sam@acme.test" },
    { service_id: "svc-sam-4", name: "checkout-worker", kind: "app", region: "eu-central-1", status: "running",
      replicas: 2, updated_at: at, tenant_id: "t_us", principal_id: "sam@acme.test" }];
  const activity = [{ type: "ticket", at, kind: "confirmed", actor: "sam@acme.test", text: "", visibility: "customer",
    ticket_id: "TKT-00007", principal_id: "sam@acme.test", tenant_id: "t_us" }];
  const claims = btoa(JSON.stringify({ sub: "lead@nimbus.example", tid: "t_us", tier: "T1_VERIFIED", exp: 4102444800 }));
  await page.addInitScript((token) => sessionStorage.setItem("support-agent:staff:token", token), `x.${claims}.y`);
  await page.route("**/v1/staff/**", async (route) => {
    const url = new URL(route.request().url()).pathname.replace("/v1/staff", "");
    const body = route.request().postDataJSON();
    if (route.request().method() === "POST") posts.push({ url, body });
    if (url === "/overview") return route.fulfill({ json: { tickets: [refund, vpn], services: services(), activity } });
    if (url === "/tickets") return route.fulfill({ json: { tickets: [refund, vpn] } });
    if (url === "/tickets/TKT-00007") return route.fulfill({ json: { ...refund, events } });
    if (url === "/tickets/TKT-00007/notes") {
      events.push({ kind: "note", actor: "staff:demo", text: body.text, at, visibility: body.visibility });
      return route.fulfill({ json: { ...refund, events } });
    }
    if (url === "/tickets/TKT-00007/status") {
      Object.assign(refund, { status: body.status });
      events.push({ kind: "status", actor: "staff:demo", text: "resolved: Refund approved: inv-sam-1 partly credited.", at, visibility: "customer" });
      return route.fulfill({ json: { ...refund, events } });
    }
    if (url === "/customers") return route.fulfill({ json: { customers: [
      { tenant_id: "t_us", principal_id: "sam@acme.test", company: "Demo Co", plan: "Business", region: "eu-central",
        since: "2025-01-01", last_seen: at, online: true, open_tickets: 1, tickets: 1 },
      { tenant_id: "t_eu", principal_id: "greta@globex.test", company: "Demo Co", plan: "Business", region: "eu-central",
        since: "2025-01-01", last_seen: null, online: false, open_tickets: 1, tickets: 1 }] } });
    if (url === "/services") return route.fulfill({ json: { services: services() } });
    if (url === "/services/t_us/svc-sam-2/actions") {
      worker = "stopping";
      return route.fulfill({ json: { service_id: "svc-sam-2", status: "stopping" } });
    }
    if (url === "/activity") return route.fulfill({ json: { activity } });
    if (url === "/handoffs") return route.fulfill({ json: { handoffs: [] } });
    return route.fulfill({ status: 404, json: { detail: "not stubbed" } });
  });
  return posts;
}

const shot = (page: Page, name: string) => process.env.SHOTS
  ? page.screenshot({ path: `${process.env.SHOTS}/staff-${name}.png`, fullPage: true }) : Promise.resolve();

test("staff work a refund ticket, look up a customer and stop a service", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (message) => message.type() === "error" && errors.push(message.text()));
  page.on("pageerror", (error) => errors.push(error.message));
  const posts = await stub(page);

  await page.goto("/staffs/");
  await expect(page.getByText("Signed in as lead@nimbus.example")).toBeVisible();
  await expect(page.getByRole("link", { name: /Open\s*1/ })).toBeVisible();
  await expect(page.getByRole("region", { name: "Open work by team" })).toContainText("Billing team");
  await shot(page, "overview");

  await page.getByRole("navigation", { name: "Staff console" }).getByRole("link", { name: "Tickets" }).click();
  await expect(page.getByRole("link", { name: "TKT-00008" })).toBeVisible();
  await shot(page, "tickets");
  await page.getByRole("searchbox").fill("sam@");
  await expect(page.getByRole("link", { name: "TKT-00008" })).toHaveCount(0);
  await page.getByRole("link", { name: "TKT-00007" }).click();

  await expect(page.getByRole("heading", { name: "TKT-00007 · Charged twice" })).toBeVisible();
  await page.getByRole("radio", { name: "Internal note" }).click();
  await page.getByRole("textbox", { name: "Internal note" }).fill("Retry bug in the processor");
  await page.getByRole("button", { name: "Add note" }).click();
  await expect(page.getByText("Retry bug in the processor")).toBeVisible();
  await expect(page.getByText("Internal", { exact: true })).toBeVisible();
  await page.getByRole("radio", { name: "Reply to customer" }).click();
  await page.getByRole("textbox", { name: "Reply to customer" }).fill("We are refunding you.");
  await page.getByRole("button", { name: "Send reply" }).click();
  await expect(page.getByText("We are refunding you.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Reject" })).toBeDisabled();        // a rejection needs a reason
  await page.getByRole("button", { name: "Approve refund" }).click();
  await expect(page.getByText("Refund approved: inv-sam-1 partly credited.")).toBeVisible();
  await shot(page, "ticket");
  expect(posts.map((p) => [p.url, p.body])).toEqual([
    ["/tickets/TKT-00007/notes", { text: "Retry bug in the processor", visibility: "internal" }],
    ["/tickets/TKT-00007/notes", { text: "We are refunding you.", visibility: "customer" }],
    ["/tickets/TKT-00007/status", { status: "resolved", reason: "", credit: true }],
  ]);

  await page.getByRole("navigation", { name: "Staff console" }).getByRole("link", { name: "Customers" }).click();
  await expect(page.getByRole("link", { name: "sam@acme.test" })).toBeVisible();
  await expect(page.getByText("Online", { exact: true })).toBeVisible();
  await page.getByRole("checkbox", { name: "Has open tickets" }).check();
  await expect(page.getByText("2 of 2 customers")).toBeVisible();
  await shot(page, "customers");

  await page.getByRole("navigation", { name: "Staff console" }).getByRole("link", { name: "Services" }).click();
  await page.getByRole("row", { name: /batch-worker-02/ }).getByRole("button", { name: "Stop" }).click();
  await expect(page.getByRole("row", { name: /batch-worker-02/ })).toContainText("Stopping");
  await shot(page, "services");
  expect(posts.at(-1)).toEqual({ url: "/services/t_us/svc-sam-2/actions", body: { action: "stop", replicas: null } });

  await page.getByRole("navigation", { name: "Staff console" }).getByRole("link", { name: "Hand-offs" }).click();
  await expect(page.getByText("No hand-offs waiting")).toBeVisible();
  expect(errors).toEqual([]); // includes Content-Security-Policy violations
});

test("the console asks for a staff sign-in and shows why a token was refused", async ({ page }) => {
  await page.route("**/v1/staff/**", (route) => route.fulfill({ status: 403, json: { detail: "staff only" } }));
  await page.goto("/staffs/");
  await expect(page.getByRole("form", { name: "Staff sign-in" })).toBeVisible();
  await expect(page.getByLabel("Work email")).toBeVisible();
  const claims = btoa(JSON.stringify({ sub: "sam@acme.test", tid: "t_us", tier: "T1_VERIFIED", exp: 4102444800 }));
  await page.evaluate((token) => sessionStorage.setItem("support-agent:staff:token", token), `x.${claims}.y`);
  await page.reload();
  await expect(page.getByRole("alert")).toContainText("Signed out: staff only");
});
