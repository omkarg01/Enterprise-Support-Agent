"""The demo staff console's API (/v1/staff/*; built outside Genesis, like the portal and the Services tab).

There is NO sign-in: anyone who can reach the API reads every customer's email, tickets and services and can
move tickets, credit invoices and act on services (register S20). It exists to show how a staff side feels;
the real one needs SSO and the staff directory (hitl.approval.verify_staff) before it holds real customers.

    GET  /v1/staff/tickets                     every raised ticket, newest first (the console filters)
    GET  /v1/staff/tickets/{TKT-n}              one ticket with its whole history, staff-only notes included
    POST /v1/staff/tickets/{TKT-n}/status       {status, reason, credit}: MOVES; credit approves a refund
    POST /v1/staff/tickets/{TKT-n}/notes        {text, visibility}: a reply the customer sees, or internal
    GET  /v1/staff/customers                    everyone with an account, a ticket, a service or a sign-in
    GET  /v1/staff/customers/{tenant}/{email}   one customer: account, invoices, tickets, services, activity
    GET  /v1/staff/services                     every server and app with its settled status
    POST /v1/staff/services/{tenant}/{id}/actions   {action, replicas}: stop, start, restart, scale
    GET  /v1/staff/activity                     recent ticket and service events
    GET  /v1/staff/handoffs                     escalations waiting for a person (hitl.queues)
    GET  /v1/staff/feedback?days=30             the portal's thumbs: summary and every rating, with the reply,
                                                the question before it and the (masked) comment

Each read runs per tenant under that tenant's row-level security, for the tenants in STAFF_TENANTS (the
tenants table is itself tenant-isolated, so it cannot be listed). Ticket texts stay as stored, PII tokens
included. Everything is returned whole and filtered in the browser.
"""

import os
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from hitl.queues import Refused, context
from hitl.tickets import RAISED, Tickets, number_id
from improvement import feedback
from tools import services

TENANTS = tuple(t.strip() for t in os.environ.get("STAFF_TENANTS", "t_us,t_eu").split(",") if t.strip())
ACTOR = "staff:demo"
TICKET = re.compile(r"^TKT-[0-9]{5,}$")
SAFE = re.compile(r"^[A-Za-z0-9_.@:+-]{1,128}$")
# ponytail: whole lists in one response, filtered in the browser; page in SQL when tickets run to thousands


class StatusBody(BaseModel):
    status: str = Field(max_length=20)
    reason: str = Field(default="", max_length=2000)
    credit: bool = False


class NoteBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    visibility: str = Field(max_length=10)


class ActionBody(BaseModel):
    action: str = Field(max_length=10)
    replicas: int | None = None


def view(ticket):
    return {**ticket.card(), "tenant_id": ticket.tenant_id, "principal_id": ticket.principal_id,
            "description": ticket.description, "reason": ticket.reason,
            "conversation_id": ticket.conversation_id, "updated_at": ticket.updated_at.isoformat(),
            "opened_at": ticket.opened_at and ticket.opened_at.isoformat(),
            **({"events": list(ticket.events)} if ticket.events else {})}


def _iso(row):
    return {k: v.isoformat() if hasattr(v, "isoformat") else v for k, v in row.items()}


def _rows(conn, sql, args):
    cursor = conn.execute(sql, args)
    names = [c.name for c in cursor.description]
    return [_iso(dict(zip(names, row, strict=True))) for row in cursor.fetchall()]


CUSTOMERS = """
WITH people AS (
  SELECT principal_id FROM nimbus_accounts WHERE tenant_id = %(t)s
  UNION SELECT principal_id FROM support_tickets WHERE tenant_id = %(t)s AND status = ANY(%(raised)s)
  UNION SELECT principal_id FROM nimbus_services WHERE tenant_id = %(t)s
  UNION SELECT principal_id FROM sessions WHERE tenant_id = %(t)s AND tier <> 'T0_ANON')
SELECT %(t)s AS tenant_id, p.principal_id, a.company, a.plan, a.region, a.since,
  (SELECT max(s.last_seen_at) FROM sessions s WHERE s.tenant_id = %(t)s AND s.principal_id = p.principal_id)
    AS last_seen,
  EXISTS (SELECT 1 FROM sessions s WHERE s.tenant_id = %(t)s AND s.principal_id = p.principal_id
          AND s.ended_at IS NULL AND s.last_seen_at > now() - interval '30 minutes') AS online,
  (SELECT count(*) FROM support_tickets k WHERE k.tenant_id = %(t)s AND k.principal_id = p.principal_id
   AND k.status IN ('open', 'in_review')) AS open_tickets,
  (SELECT count(*) FROM support_tickets k WHERE k.tenant_id = %(t)s AND k.principal_id = p.principal_id
   AND k.status = ANY(%(raised)s)) AS tickets
FROM people p LEFT JOIN nimbus_accounts a ON a.tenant_id = %(t)s AND a.principal_id = p.principal_id
ORDER BY p.principal_id
"""
TICKET_EVENTS = """
SELECT e.at, e.kind, e.actor, e.text, e.visibility, t.id AS ticket, t.principal_id, %(t)s AS tenant_id
FROM ticket_events e JOIN support_tickets t ON t.id = e.ticket_id
WHERE e.tenant_id = %(t)s AND t.status = ANY(%(raised)s) ORDER BY e.id DESC LIMIT 40
"""
HANDOFFS = """
SELECT id::text, %(t)s AS tenant_id, queue, reason, acuity, customer_id, conversation_id, status, assignee_id,
       created_at, sla_due_at FROM escalations
WHERE tenant_id = %(t)s AND status IN ('waiting', 'claimed') ORDER BY acuity DESC, sla_due_at
"""


def staff_router(connect, tenants=TENANTS):
    router = APIRouter(prefix="/v1/staff")
    tickets = Tickets(connect)

    def each(sql):
        """One query per tenant, in one connection, each under its tenant's row-level security."""
        out = []
        with connect() as conn:
            for tenant in tenants:
                with conn.transaction():
                    context(conn, tenant)
                    out += _rows(conn, sql, {"t": tenant, "raised": list(RAISED)})
        return out

    def find(number):
        """(tenant, ticket id) of a raised ticket, else 404 (ticket ids are unique across tenants)."""
        ticket_id = number_id(number) if TICKET.match(number) else None
        for tenant in tenants if ticket_id else ():
            if tickets.staff_get(tenant, ticket_id):
                return tenant, ticket_id
        raise HTTPException(404, "no such ticket")

    def refused(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Refused as error:
            raise HTTPException(409, str(error)) from None

    @router.get("/tickets")
    def all_tickets():
        return {"tickets": [view(t) for tenant in tenants for t in tickets.raised(tenant)]}

    @router.get("/tickets/{number}")
    def one_ticket(number: str):
        return view(tickets.staff_get(*find(number)))

    @router.post("/tickets/{number}/status")
    def move(number: str, body: StatusBody):
        tenant, ticket_id = find(number)
        return view(refused(tickets.move, tenant, ticket_id, body.status, ACTOR, body.reason, body.credit))

    @router.post("/tickets/{number}/notes")
    def note(number: str, body: NoteBody):
        tenant, ticket_id = find(number)
        return view(refused(tickets.staff_note, tenant, ticket_id, ACTOR, body.text, body.visibility))

    @router.get("/customers")
    def customers():
        return {"customers": each(CUSTOMERS)}

    @router.get("/customers/{tenant}/{email}")
    def customer(tenant: str, email: str):
        if tenant not in tenants or not SAFE.match(email):
            raise HTTPException(404, "no such customer")
        args = {"t": tenant, "p": email}
        with connect() as conn, conn.transaction():
            context(conn, tenant)
            account = _rows(conn, "SELECT company, plan, region, since, account_id FROM nimbus_accounts "
                                  "WHERE tenant_id = %(t)s AND principal_id = %(p)s", args)
            invoices = _rows(conn, "SELECT invoice_id, issued_on, amount_usd::float AS amount_usd, status "
                                   "FROM nimbus_invoices WHERE tenant_id = %(t)s AND principal_id = %(p)s "
                                   "ORDER BY issued_on DESC", args)
        own = [view(t) for t in tickets.raised(tenant) if t.principal_id == email]
        found = services.services(connect, tenant, email)
        if not (account or invoices or own or found):
            raise HTTPException(404, "no such customer")
        return {"tenant_id": tenant, "principal_id": email, "account": account[0] if account else None,
                "invoices": invoices, "tickets": own, "services": found,
                "activity": services.activity(connect, tenant, email)}

    @router.get("/services")
    def all_services():
        return {"services": [{"tenant_id": tenant, **s} for tenant in tenants
                             for s in services.everyone(connect, tenant)]}

    @router.post("/services/{tenant}/{service_id}/actions")
    def act(tenant: str, service_id: str, body: ActionBody):
        if tenant not in tenants or not SAFE.match(service_id):
            raise HTTPException(404, "no such service")
        return refused(services.staff_act, connect, tenant, service_id, body.action, body.replicas, ACTOR)

    @router.get("/activity")
    def activity():
        ticket_events = [{**e, "ticket_id": f"TKT-{e.pop('ticket'):05d}", "type": "ticket"}
                         for e in each(TICKET_EVENTS)]
        service_events = [{**a, "tenant_id": tenant, "type": "service"} for tenant in tenants
                          for a in services.activity(connect, tenant, limit=40)]
        return {"activity": sorted(ticket_events + service_events, key=lambda e: e["at"], reverse=True)[:60]}

    @router.get("/handoffs")
    def handoffs():
        return {"handoffs": each(HANDOFFS)}

    @router.get("/feedback")
    def ratings(days: int = Query(30, ge=1, le=365)):
        rows = feedback.ratings(connect, tenants, datetime.now(timezone.utc) - timedelta(days=days))
        return {"days": days, "summary": feedback.summary(rows), "ratings": rows}

    return router
