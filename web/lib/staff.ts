// The demo staff console (/staffs; the /v1/staff API, channels.api.staff): its shapes and calls, and the
// filtering, sorting, paging and counting its pages share. Everything but staffApi is pure.
// NO sign-in: the API is open to anyone (register S20). It exists to show how a staff side would feel.
import type { Service, ServiceActivity } from "./services.ts";
import type { Ticket, TicketEvent, TicketStatus } from "./tickets.ts";

export type StaffTicket = Ticket & {
  tenant_id: string;
  principal_id: string;
  description: string;
  reason: string;
  conversation_id: string;
  opened_at: string | null;
  updated_at: string;
  events?: TicketEvent[];      // only on a single ticket
};
export type Customer = {
  tenant_id: string;
  principal_id: string;
  company: string | null;
  plan: string | null;
  region: string | null;
  since: string | null;
  last_seen: string | null;
  online: boolean;
  open_tickets: number;
  tickets: number;
};
export type StaffService = Service & { tenant_id: string; principal_id: string };
export type Invoice = { invoice_id: string; issued_on: string; amount_usd: number; status: string };
export type CustomerDetail = {
  tenant_id: string;
  principal_id: string;
  account: { company: string; plan: string; region: string; since: string; account_id: string } | null;
  invoices: Invoice[];
  tickets: StaffTicket[];
  services: Service[];
  activity: ServiceActivity[];
};
export type Activity =
  | { type: "ticket"; at: string; kind: TicketEvent["kind"]; actor: string; text: string;
      visibility: "customer" | "internal"; ticket_id: string; principal_id: string; tenant_id: string }
  | (ServiceActivity & { type: "service"; tenant_id: string; principal_id: string });
export type Handoff = {
  id: string; tenant_id: string; queue: string; reason: string; acuity: number; customer_id: string;
  conversation_id: string; status: string; assignee_id: string | null; created_at: string; sla_due_at: string;
};

// The portal's thumbs (improvement.feedback.ratings): the reply and its question as History keeps them, the
// comment masked, its failure category and severity (0..1) from Jev.
export type Rating = {
  id: number; tenant_id: string; conversation_id: string; turn_id: string; signal: "up" | "down";
  comment: string | null; category: string | null; severity: number | null; at: string;
  question: string | null; answer: string | null; citations: { doc_id: string; title?: string }[]; models: string[];
};
export type Rate = { up: number; down: number; up_rate: number | null };
export type FeedbackReport = {
  days: number;
  summary: { total: Rate; by_day: Record<string, Rate>; by_model: Record<string, Rate>; down_by_category: Record<string, number> };
  ratings: Rating[];
};
export const CRITICAL = 0.8; // improvement.feedback.PAGE_AT

export class StaffError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function call<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
  const reply = await fetch(`/v1/staff${path}`, {
    method, cache: "no-store", body: body === undefined ? undefined : JSON.stringify(body),
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
  });
  const data = await reply.json().catch(() => ({}));
  if (!reply.ok) throw new StaffError(reply.status, typeof data.detail === "string" ? data.detail : `HTTP ${reply.status}`);
  return data as T;
}

const enc = encodeURIComponent;
export const staffApi = {
  tickets: () => call<{ tickets: StaffTicket[] }>("GET", "/tickets").then((d) => d.tickets),
  ticket: (id: string) => call<StaffTicket>("GET", `/tickets/${enc(id)}`),
  move: (id: string, status: TicketStatus, reason = "", credit = false) =>
    call<StaffTicket>("POST", `/tickets/${enc(id)}/status`, { status, reason, credit }),
  note: (id: string, text: string, visibility: "customer" | "internal") =>
    call<StaffTicket>("POST", `/tickets/${enc(id)}/notes`, { text, visibility }),
  customers: () => call<{ customers: Customer[] }>("GET", "/customers").then((d) => d.customers),
  customer: (tenant: string, email: string) => call<CustomerDetail>("GET", `/customers/${enc(tenant)}/${enc(email)}`),
  services: () => call<{ services: StaffService[] }>("GET", "/services").then((d) => d.services),
  act: (tenant: string, serviceId: string, action: ServiceActivity["action"], replicas: number | null = null) =>
    call<Service>("POST", `/services/${enc(tenant)}/${enc(serviceId)}/actions`, { action, replicas }),
  activity: () => call<{ activity: Activity[] }>("GET", "/activity").then((d) => d.activity),
  handoffs: () => call<{ handoffs: Handoff[] }>("GET", "/handoffs").then((d) => d.handoffs),
  feedback: (days = 30) => call<FeedbackReport>("GET", `/feedback?days=${days}`),
};

// Thumbs down first by severity (critical on top, unknown last), then newest.
export const worstFirst = (ratings: Rating[]) => ratings.filter((r) => r.signal === "down")
  .sort((a, b) => (b.severity ?? -1) - (a.severity ?? -1) || b.at.localeCompare(a.at));
export const percent = (rate: number | null) => rate === null ? "—" : `${Math.round(rate * 100)}%`;

export const ticketHref = (id: string) => `/staffs/tickets/${enc(id)}`;
export const customerHref = (tenant: string, email: string) => `/staffs/customers/${enc(tenant)}/${enc(email)}`;
export const ACTIVE: TicketStatus[] = ["open", "in_review"];
export const STATUSES: TicketStatus[] = ["open", "in_review", "resolved", "rejected", "closed"];

// The buttons a ticket offers, following hitl.tickets.MOVES (the API refuses anything else).
export type Move = { to: TicketStatus; label: string; credit?: boolean; needsReason?: boolean; tone: "primary" | "plain" | "danger" };
export function movesFor(ticket: Pick<StaffTicket, "status" | "category" | "invoice_id">): Move[] {
  const refund = ticket.category === "refund" && !!ticket.invoice_id;
  const resolve: Move[] = refund
    ? [{ to: "resolved", label: "Approve refund", credit: true, tone: "primary" }, { to: "resolved", label: "Resolve", tone: "plain" }]
    : [{ to: "resolved", label: "Resolve", tone: "primary" }];
  const reject: Move = { to: "rejected", label: "Reject", needsReason: true, tone: "danger" };
  switch (ticket.status) {
    case "open": return [{ to: "in_review", label: "Start review", tone: "plain" }, ...resolve, reject,
      { to: "closed", label: "Close", tone: "plain" }];
    case "in_review": return [...resolve, reject, { to: "open", label: "Back to open", tone: "plain" }];
    case "resolved": return [{ to: "closed", label: "Close", tone: "primary" }, { to: "open", label: "Reopen", tone: "plain" }];
    case "rejected": return [{ to: "open", label: "Reopen", tone: "plain" }];
    default: return [];
  }
}
export const canMove = (ticket: Pick<StaffTicket, "status" | "category" | "invoice_id">, to: TicketStatus) =>
  movesFor(ticket).some((move) => move.to === to);

// Tickets: filters, sort, pages ------------------------------------------------------------------------
export type TicketFilter = {
  q: string; status: "all" | "active" | TicketStatus; category: string; queue: string; tenant: string;
  from: string; to: string;    // yyyy-mm-dd, inclusive, on the ticket's creation day
};
export const NO_FILTER: TicketFilter = { q: "", status: "all", category: "all", queue: "all", tenant: "all", from: "", to: "" };
export type TicketSort = "newest" | "oldest" | "updated" | "amount";

const day = (at: string) => at.slice(0, 10);
export function filterTickets(tickets: StaffTicket[], f: TicketFilter) {
  const q = f.q.trim().toLowerCase();
  return tickets.filter((t) =>
    (f.status === "all" || (f.status === "active" ? ACTIVE.includes(t.status) : t.status === f.status))
    && (f.category === "all" || t.category === f.category)
    && (f.queue === "all" || t.queue === f.queue)
    && (f.tenant === "all" || t.tenant_id === f.tenant)
    && (!f.from || day(t.created_at) >= f.from) && (!f.to || day(t.created_at) <= f.to)
    && (!q || [t.ticket_id, t.principal_id, t.subject, t.invoice_id ?? ""].some((v) => v.toLowerCase().includes(q))));
}

export function sortTickets(tickets: StaffTicket[], by: TicketSort) {
  const time = (at: string) => new Date(at).getTime();
  const order: Record<TicketSort, (a: StaffTicket, b: StaffTicket) => number> = {
    newest: (a, b) => time(b.created_at) - time(a.created_at),
    oldest: (a, b) => time(a.created_at) - time(b.created_at),
    updated: (a, b) => time(b.updated_at) - time(a.updated_at),
    amount: (a, b) => (b.amount_cents ?? -1) - (a.amount_cents ?? -1),
  };
  return [...tickets].sort(order[by]);
}

export function paginate<T>(items: T[], page: number, size = 20) {
  const pages = Math.max(1, Math.ceil(items.length / size));
  const current = Math.min(Math.max(1, page), pages);
  return { items: items.slice((current - 1) * size, current * size), page: current, pages };
}

// Distinct values of a field, sorted, for a filter's options.
export const optionsOf = <T,>(items: T[], pick: (item: T) => string | null | undefined) =>
  [...new Set(items.map(pick).filter((v): v is string => !!v))].sort();

// Overview counts ------------------------------------------------------------------------------------------
export function countBy<T>(items: T[], pick: (item: T) => string) {
  const counts: Record<string, number> = {};
  for (const item of items) counts[pick(item)] = (counts[pick(item)] ?? 0) + 1;
  return Object.entries(counts).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

export function summary(tickets: StaffTicket[], now = new Date()) {
  const today = now.toISOString().slice(0, 10);
  const active = tickets.filter((t) => ACTIVE.includes(t.status));
  const oldest = active.reduce<StaffTicket | null>((a, t) => (!a || t.created_at < a.created_at ? t : a), null);
  return {
    open: tickets.filter((t) => t.status === "open").length,
    inReview: tickets.filter((t) => t.status === "in_review").length,
    resolvedToday: tickets.filter((t) => t.status === "resolved" && day(t.updated_at) === today).length,
    rejected: tickets.filter((t) => t.status === "rejected").length,
    oldestOpen: oldest,
    byQueue: countBy(active, (t) => t.queue),
    byCategory: countBy(active, (t) => t.category),
  };
}

// Services ---------------------------------------------------------------------------------------------------
export type ServiceFilter = { q: string; kind: string; status: string; region: string; tenant: string };
export const NO_SERVICE_FILTER: ServiceFilter = { q: "", kind: "all", status: "all", region: "all", tenant: "all" };
export function filterServices(services: StaffService[], f: ServiceFilter) {
  const q = f.q.trim().toLowerCase();
  return services.filter((s) => (f.kind === "all" || s.kind === f.kind)
    && (f.status === "all" || s.status === f.status) && (f.region === "all" || s.region === f.region)
    && (f.tenant === "all" || s.tenant_id === f.tenant)
    && (!q || [s.name, s.principal_id, s.service_id].some((v) => v.toLowerCase().includes(q))));
}

// What staff can do to a service right now (tools.services.ACTIONS): the action and whether it needs replicas.
export function actionsFor(service: Pick<Service, "kind" | "status">): ServiceActivity["action"][] {
  if (service.status === "running") return service.kind === "server" ? ["stop", "restart"] : ["restart", "scale"];
  return service.kind === "server" && service.status === "stopped" ? ["start"] : [];
}

// running / busy (a change settling) / stopped, per customer, for the customers table.
export function serviceHealth(services: Pick<StaffService, "status">[]) {
  const busy = new Set(["stopping", "starting", "restarting", "scaling"]);
  return {
    running: services.filter((s) => s.status === "running").length,
    busy: services.filter((s) => busy.has(s.status)).length,
    stopped: services.filter((s) => s.status === "stopped").length,
  };
}

// Customers ------------------------------------------------------------------------------------------------
export type CustomerFilter = { q: string; tenant: string; plan: string; openOnly: boolean };
export const NO_CUSTOMER_FILTER: CustomerFilter = { q: "", tenant: "all", plan: "all", openOnly: false };
export function filterCustomers(customers: Customer[], f: CustomerFilter) {
  const q = f.q.trim().toLowerCase();
  return customers.filter((c) => (f.tenant === "all" || c.tenant_id === f.tenant)
    && (f.plan === "all" || c.plan === f.plan) && (!f.openOnly || c.open_tickets > 0)
    && (!q || [c.principal_id, c.company ?? ""].some((v) => v.toLowerCase().includes(q))));
}

// What happened, after who did it: "Assistant offered the ticket", "Staff added an internal note".
const DID: Record<string, string> = {
  proposed: "offered the ticket", confirmed: "raised the ticket", status: "changed the status",
};
export const didWhat = (event: Pick<TicketEvent, "kind" | "visibility">) =>
  event.kind === "note" ? (event.visibility === "internal" ? "added an internal note" : "wrote") : DID[event.kind] ?? event.kind;

// Who did something, as staff read it.
export const actorLabel = (actor: string) =>
  actor === "agent" ? "Assistant" : actor.startsWith("staff:") ? "Staff" : "Customer";
