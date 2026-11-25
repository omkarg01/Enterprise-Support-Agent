// The portal in a real browser with /v1 stubbed: a checked reply with its sources, the citation drawer, the
// execution trace, under the production Content-Security-Policy. SHOTS=dir saves screenshots.
import { expect, test, type Page } from "@playwright/test";

const token = (tier: string) =>
  `h.${Buffer.from(JSON.stringify({ sub: "sam@acme.test", tid: "t_us", tier, exp: 4e9 })).toString("base64url")}.s`;

const TRACE = {
  turn_id: "turn-1", detail: "full", tier: "T1_VERIFIED",
  summary: "Answered from our documentation after the output checks passed.",
  final: { label: "Final answer", outcome: "composed" },
  models: ["claude-haiku-5-5"],
  agents: [
    { id: "coordinator", name: "Coordinator", role: "Screens the message and routes it", status: "success",
      outcome: "routed to the Knowledge Agent", notes: ["Stages: Triaged → Deliberating → Resolved"],
      calls: [{ name: "Safety screening", args: {}, result: "PASS", items: [] },
        { name: "Triage", args: { intent: "Billing question", by: "decision engine" }, result: "SUCCESS", items: [] }] },
    { id: "knowledge", name: "Knowledge Agent", role: "Finds documentation passages and drafts the answer",
      status: "success", outcome: "retrieved 2 sources", notes: [],
      calls: [{ name: "Retrieval lookup", args: { index: "public_knowledge_v1", query: "refund recent subscription" },
        result: "SUCCESS", items: ["How refunds work", "Billing FAQ"] },
      { name: "Draft answer", args: {}, result: "SUCCESS", items: [] }] },
    { id: "reviewer", name: "Output Reviewer", role: "Checks the reply before it is sent", status: "success",
      outcome: "verdict PASS", notes: [], calls: [{ name: "Output checks", args: {}, result: "PASS", items: [] }] },
  ],
  shared_state: { user_query: "refund recent subscription", intent: "Billing question", sources: 2, retry_count: 0,
    final_answer: "composed", requested_at: "2026-11-01T10:24:00+00:00" },
};

const EVENTS = [
  ["status", { turn_id: "turn-1", text: "Looking into it" }],
  ["citation", { turn_id: "turn-1", doc_id: "kb-refunds", title: "How refunds work", heading: "Requesting a refund",
    excerpt: "Customers may request a refund within 30 days of purchase by submitting a request in the portal." }],
  ["citation", { turn_id: "turn-1", doc_id: "kb-billing-faq", title: "Billing FAQ" }],
  ["final", { turn_id: "turn-1", role: "assistant", ai_generated: true,
    text: "Yes, you may be eligible for a refund if your request is within 30 days of the charge.",
    actions: [{ id: "talk_to_human", label: "Talk to a human" }] }], // still sent by the server; not shown
].map(([event, data], i) => `id: ${i + 1}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join("");

async function stub(page: Page, tier = "T1_VERIFIED") {
  let streamed = false;
  await page.route("**/v1/**", async (route) => {
    const url = route.request().url();
    if (url.endsWith("/identity/anonymous")) return route.fulfill({ json: { access_token: token(tier) } });
    if (url.endsWith("/events")) {
      const body = streamed ? "" : EVENTS;
      streamed = true;
      return route.fulfill({ body, contentType: "text/event-stream" });
    }
    if (url.includes("/trace")) return route.fulfill({ json: TRACE });
    return route.fulfill({ status: 202, json: { turn_id: "turn-2", status: "QUEUED" } });
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

test("a reply shows its sources and how it was answered", async ({ page }) => {
  const errors = watchErrors(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await stub(page);
  await page.goto("/");

  await expect(page.getByText("You are chatting with an AI assistant.")).toBeVisible();
  await expect(page.getByText("within 30 days of the charge")).toBeVisible();
  await expect(page.getByText("AI-assisted")).toBeVisible();
  await expect(page.getByLabel("Signed in as sam@acme.test")).toBeVisible();

  await page.getByRole("button", { name: "How this was answered" }).click();
  const drawer = page.getByRole("dialog");
  await expect(drawer.getByText("Execution tree")).toBeVisible();
  await expect(drawer.getByText("Model used")).toBeVisible();
  await expect(drawer.getByText("claude-haiku-5-5")).toBeVisible();
  await expect(drawer.getByText("Retrieval lookup")).toBeVisible();
  await expect(drawer.getByText("query: refund recent subscription")).toBeVisible();
  await drawer.getByText("Shared state (6 items)").click();
  await expect(drawer.getByText("retry_count")).toBeVisible();
  await shot(page, "trace");

  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("list", { name: "Sources" }).getByRole("button", { name: /How refunds work/ }).click();
  await expect(page.getByRole("dialog").getByText("Customers may request a refund")).toBeVisible();
  await shot(page, "citation");
  await page.getByRole("tab", { name: "Details" }).click();
  await expect(page.getByRole("dialog").getByText("kb-refunds")).toBeVisible();

  expect(errors).toEqual([]); // includes Content-Security-Policy violations
});

test("a reply offers no why view or talk-to-human button, and the phone layout has no sideways scroll", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await stub(page, "T0_ANON");
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  await expect(page.getByText("within 30 days of the charge")).toBeVisible();
  await expect(page.getByRole("button", { name: /Why this answer|Talk to a human/ })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await shot(page, "phone");
});
