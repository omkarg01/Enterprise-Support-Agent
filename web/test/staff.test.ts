// The staff console's pure parts (lib/staff.ts) and the portal's new ticket labels (lib/tickets.ts).
import { expect, test } from "vitest";

import {
  NO_CUSTOMER_FILTER, NO_FILTER, NO_SERVICE_FILTER, actionsFor, actorLabel, canMove, countBy, didWhat, filterCustomers,
  filterServices, filterTickets, movesFor, optionsOf, paginate, percent, serviceHealth, sortTickets, summary, worstFirst,
  type Customer, type Rating, type StaffService, type StaffTicket,
} from "../lib/staff.ts";
import { doneBy } from "../lib/services.ts";
import { eventText, eventTitle, statusLabel } from "../lib/tickets.ts";

const ticket = (id: number, patch: Partial<StaffTicket> = {}): StaffTicket => ({
  ticket_id: `TKT-${String(id).padStart(5, "0")}`, status: "open", category: "refund", subject: `Subject ${id}`,
  invoice_id: `inv-${id}`, amount_cents: id * 100, currency: "USD", queue: "queue_billing",
  created_at: `2026-11-${String(10 + id).padStart(2, "0")}T10:00:00Z`, tenant_id: "t_us", principal_id: `p${id}@x.com`,
  description: "", reason: "refund request", conversation_id: `c${id}`, opened_at: null,
  updated_at: `2026-11-${String(10 + id).padStart(2, "0")}T12:00:00Z`, ...patch,
});
const TICKETS = [
  ticket(1), ticket(2, { status: "in_review", tenant_id: "t_eu" }), ticket(3, { status: "resolved", category: "technical", queue: "queue_technical", invoice_id: null }),
  ticket(4, { status: "rejected" }), ticket(5, { status: "closed", amount_cents: null }),
];

test("ticket filters combine status, category, team, tenant, dates and search", () => {
  expect(filterTickets(TICKETS, NO_FILTER)).toHaveLength(5);
  expect(filterTickets(TICKETS, { ...NO_FILTER, status: "active" }).map((t) => t.ticket_id)).toEqual(["TKT-00001", "TKT-00002"]);
  expect(filterTickets(TICKETS, { ...NO_FILTER, category: "technical" }).map((t) => t.queue)).toEqual(["queue_technical"]);
  expect(filterTickets(TICKETS, { ...NO_FILTER, tenant: "t_eu" })).toHaveLength(1);
  expect(filterTickets(TICKETS, { ...NO_FILTER, from: "2026-11-12", to: "2026-11-13" }).map((t) => t.ticket_id))
    .toEqual(["TKT-00002", "TKT-00003"]);
  expect(filterTickets(TICKETS, { ...NO_FILTER, q: "P4@X" }).map((t) => t.ticket_id)).toEqual(["TKT-00004"]);
  expect(filterTickets(TICKETS, { ...NO_FILTER, q: "inv-2" }).map((t) => t.ticket_id)).toEqual(["TKT-00002"]);
});

test("tickets sort newest, oldest, by update and by amount (no amount last), and page", () => {
  expect(sortTickets(TICKETS, "newest")[0].ticket_id).toBe("TKT-00005");
  expect(sortTickets(TICKETS, "oldest")[0].ticket_id).toBe("TKT-00001");
  expect(sortTickets(TICKETS, "amount").map((t) => t.amount_cents)).toEqual([400, 300, 200, 100, null]);
  expect(paginate([1, 2, 3, 4, 5], 2, 2)).toEqual({ items: [3, 4], page: 2, pages: 3 });
  expect(paginate([1, 2], 9, 2)).toEqual({ items: [1, 2], page: 1, pages: 1 });   // past the end: the last page
  expect(paginate([], 1).pages).toBe(1);
});

test("the buttons follow the backend's moves, and only a refund with an invoice can be approved", () => {
  expect(movesFor(TICKETS[0]).map((m) => m.label)).toEqual(["Start review", "Approve refund", "Resolve", "Reject", "Close"]);
  expect(movesFor({ ...TICKETS[0], invoice_id: null }).map((m) => m.label)).toEqual(["Start review", "Resolve", "Reject", "Close"]);
  expect(movesFor(TICKETS[0]).find((m) => m.label === "Approve refund")).toMatchObject({ to: "resolved", credit: true });
  expect(movesFor(TICKETS[0]).find((m) => m.to === "rejected")?.needsReason).toBe(true);
  expect(movesFor(TICKETS[2]).map((m) => m.to)).toEqual(["closed", "open"]);
  expect(movesFor(TICKETS[3]).map((m) => m.to)).toEqual(["open"]);
  expect(movesFor(TICKETS[4])).toEqual([]);                                       // closed is final
  expect(canMove(TICKETS[3], "closed")).toBe(false);
  expect(canMove(TICKETS[0], "closed")).toBe(true);
});

test("the overview counts open work by team and category and finds who waits longest", () => {
  const now = new Date("2026-11-13T18:00:00Z");
  const s = summary(TICKETS, now);
  expect([s.open, s.inReview, s.resolvedToday, s.rejected]).toEqual([1, 1, 1, 1]);
  expect(s.oldestOpen?.ticket_id).toBe("TKT-00001");
  expect(s.byQueue).toEqual([["queue_billing", 2]]);
  expect(countBy(["b", "a", "b"], (x) => x)).toEqual([["b", 2], ["a", 1]]);
  expect(optionsOf(TICKETS, (t) => t.tenant_id)).toEqual(["t_eu", "t_us"]);
  expect(summary([], now).oldestOpen).toBeNull();
});

const service = (name: string, patch: Partial<StaffService> = {}): StaffService => ({
  service_id: `svc-${name}`, name, kind: "server", region: "eu-central-1", status: "running", replicas: null,
  updated_at: "2026-11-18T10:00:00Z", tenant_id: "t_us", principal_id: "sarah", ...patch,
});

test("service actions match what tools.services allows for the kind and status", () => {
  expect(actionsFor(service("a"))).toEqual(["stop", "restart"]);
  expect(actionsFor(service("a", { status: "stopped" }))).toEqual(["start"]);
  expect(actionsFor(service("a", { status: "stopping" }))).toEqual([]);
  expect(actionsFor(service("a", { kind: "app", replicas: 2 }))).toEqual(["restart", "scale"]);
  expect(actionsFor(service("a", { kind: "app", status: "stopped" }))).toEqual([]);
  const all = [service("api"), service("web", { status: "stopped", tenant_id: "t_eu" }), service("w", { status: "scaling" })];
  expect(serviceHealth(all)).toEqual({ running: 1, busy: 1, stopped: 1 });
  expect(filterServices(all, { ...NO_SERVICE_FILTER, tenant: "t_eu" }).map((s) => s.name)).toEqual(["web"]);
  expect(filterServices(all, { ...NO_SERVICE_FILTER, q: "SVC-API" }).map((s) => s.name)).toEqual(["api"]);
});

test("customers filter by tenant, plan, open tickets and search", () => {
  const person = (principal_id: string, patch: Partial<Customer> = {}): Customer => ({
    tenant_id: "t_us", principal_id, company: "Demo Co", plan: "Business", region: "eu-central", since: null,
    last_seen: null, online: false, open_tickets: 0, tickets: 0, ...patch,
  });
  const people = [person("a@x.com", { open_tickets: 1 }), person("b@y.com", { tenant_id: "t_eu", company: "Globex" })];
  expect(filterCustomers(people, { ...NO_CUSTOMER_FILTER, openOnly: true }).map((c) => c.principal_id)).toEqual(["a@x.com"]);
  expect(filterCustomers(people, { ...NO_CUSTOMER_FILTER, q: "globex" }).map((c) => c.principal_id)).toEqual(["b@y.com"]);
  expect(filterCustomers(people, { ...NO_CUSTOMER_FILTER, tenant: "t_us" })).toHaveLength(1);
});

test("the customer's timeline names staff replies and shows why staff changed the status", () => {
  const at = "2026-11-18T10:00:00Z";
  expect(eventTitle({ kind: "note", actor: "staff:demo", text: "On it", at })).toBe("Reply from support");
  expect(eventTitle({ kind: "note", actor: "sarah", text: "Any news?", at })).toBe("Message added");
  expect(eventText({ kind: "status", actor: "staff:demo", text: "rejected: not a duplicate", at })).toBe("rejected: not a duplicate");
  expect(eventText({ kind: "proposed", actor: "agent", text: "Charged twice", at })).toBe("");
  expect(statusLabel("rejected")).toBe("Rejected");
  expect(didWhat({ kind: "proposed" })).toBe("offered the ticket");
  expect(didWhat({ kind: "note", visibility: "internal" })).toBe("added an internal note");
  expect(actorLabel("agent")).toBe("Assistant");
  expect(actorLabel("staff:demo")).toBe("Staff");
  expect(actorLabel("sarah@x.com")).toBe("Customer");
  expect(doneBy({ action_id: "ACT-00001", service: "a", action: "stop", replicas: null, conversation_id: "staff", at, actor: "staff:demo" }))
    .toBe("Support staff");
});

test("thumbs down list most severe first, unknown severity last, then newest", () => {
  const rating = (id: number, signal: "up" | "down", severity: number | null, at: string): Rating => ({
    id, tenant_id: "t_us", conversation_id: "c", turn_id: `t${id}`, signal, comment: null, category: null, severity, at,
    question: "q", answer: "a", citations: [], models: [],
  });
  const sorted = worstFirst([
    rating(1, "down", null, "2026-11-20T10:00:00Z"), rating(2, "up", 1, "2026-11-20T11:00:00Z"),
    rating(3, "down", 0.25, "2026-11-19T10:00:00Z"), rating(4, "down", 1, "2026-11-18T10:00:00Z"),
    rating(5, "down", 0.25, "2026-11-20T09:00:00Z"),
  ]);
  expect(sorted.map((r) => r.id)).toEqual([4, 5, 3, 1]);
  expect([percent(null), percent(2 / 3)]).toEqual(["—", "67%"]);
});
