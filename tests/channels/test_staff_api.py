"""The demo staff console's API (channels.api.staff, outside Genesis): every tenant's tickets, customers and
services in one place, tickets moved along the allowed steps, refunds credited, staff-only notes kept from the
customer, and staff service actions under the same rules as the customer's own. Every route needs a staff
sign-in: a one-time-code session whose email is on the staff list."""

import subprocess
from pathlib import Path

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from channels import sessions
from channels.api.staff import staff_router
from channels.identity import otp
from channels.identity.service import Identity
from data.migrate import migrate, psql
from hitl import queues, tickets
from hitl.tickets import Tickets
from tools import nimbus, services
from tools.validation import approvals

ROOT = Path(__file__).resolve().parents[2]
DB = "test_staff_api"
DSN = f"postgresql://agent:change-me@127.0.0.1:5432/{DB}"  # credentials from .env.example
LEAD = "lead@nimbus.example"
REFUND = {"category": "refund", "subject": "Charged twice", "queue": "queue_billing",
          "reason": "refund request",
          "invoice_id": "inv-sarah-1", "amount_cents": 50000, "currency": "USD"}


def connect():
    return psycopg.connect(DSN, autocommit=True)


@pytest.fixture(scope="module")
def client():
    subprocess.run(["docker", "compose", "-f", "deploy/compose.yaml", "--env-file", ".env.example", "up",
                    "-d", "--wait", "postgres"], cwd=ROOT, check=True, capture_output=True)
    psql("postgres", f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)')
    psql("postgres", f'CREATE DATABASE "{DB}"')
    migrate("main", DB)
    for module in (sessions, otp, approvals, queues, tickets, nimbus, services):
        module.apply_schema(DB)
    psql(DB, "INSERT INTO tenants (id, name, region) VALUES ('t_us', 'Acme', 'US'), ('t_eu', 'Globex', 'EU')")
    for tenant, principal in (("t_us", "sarah"), ("t_eu", "greta")):
        nimbus.seed(connect, tenant, principal)
        services.seed(connect, tenant, principal)
    store = Tickets(connect)
    store.confirm("t_us", "sarah", store.propose("t_us", "sarah", "c1", "k", **REFUND).id)
    store.confirm("t_eu", "greta", store.propose("t_eu", "greta", "c2", "k", **{
        **REFUND, "category": "technical", "invoice_id": None, "amount_cents": None, "currency": None,
        "subject": "VPN drops"}).id)
    store.propose("t_us", "sarah", "c3", "k", **REFUND)                      # an offer only: never listed
    with connect() as conn, conn.transaction():
        conn.execute("SET LOCAL ROLE app_runtime")
        conn.execute("SELECT set_config('app.current_tenant_id', 't_us', true)")
        sessions.start(conn, "t_us", "sarah", "T1_VERIFIED")
    identity, codes = Identity(connect, send_otp=lambda tenant, user, code: codes.append(code)), []
    app = FastAPI()
    app.include_router(staff_router(connect, identity.authenticate, tenants=("t_us", "t_eu"),
                                    staff=frozenset({LEAD})))
    app.state.identity, app.state.codes = identity, codes
    return TestClient(app, headers={"Authorization": f"Bearer {signed_in(app, LEAD.upper())}"})


def signed_in(app, email):
    """An access token from the one-time code sign-in, as the staff console gets one."""
    identity, codes = app.state.identity, app.state.codes
    challenge = identity.start_otp("t_us", email)
    return identity.verify_otp("t_us", challenge, codes[-1])


def test_only_a_signed_in_email_on_the_staff_list_gets_in(client):
    app = client.app
    for headers, status in (({"Authorization": ""}, 401), ({"Authorization": "Bearer nonsense"}, 401),
                            ({"Authorization": f"Bearer {app.state.identity.anonymous('t_us')}"}, 403),
                            ({"Authorization": f"Bearer {signed_in(app, 'sarah')}"}, 403)):
        for path in ("/v1/staff/tickets", "/v1/staff/overview", "/v1/staff/customers"):
            assert client.get(path, headers=headers).status_code == status, (headers, path)
        assert client.post("/v1/staff/tickets/TKT-00001/status", json={"status": "closed"},
                           headers=headers).status_code == status
    assert client.get("/v1/staff/tickets").status_code == 200      # the lead, any letter case


def test_the_overview_is_tickets_services_and_activity_in_one_call(client):
    page = client.get("/v1/staff/overview").json()
    assert page["tickets"] == client.get("/v1/staff/tickets").json()["tickets"]
    assert page["services"] == client.get("/v1/staff/services").json()["services"]
    assert page["activity"] == client.get("/v1/staff/activity").json()["activity"]


def number(client, subject):
    return next(t["ticket_id"] for t in client.get("/v1/staff/tickets").json()["tickets"]
                if t["subject"] == subject)


def test_staff_see_every_tenants_raised_tickets_but_no_offers(client):
    listed = client.get("/v1/staff/tickets").json()["tickets"]
    assert sorted((t["tenant_id"], t["principal_id"], t["subject"], t["status"]) for t in listed) == [
        ("t_eu", "greta", "VPN drops", "open"), ("t_us", "sarah", "Charged twice", "open")]
    assert client.get("/v1/staff/tickets/TKT-99999").status_code == 404
    assert client.get("/v1/staff/tickets/nonsense").status_code == 404


def test_a_refund_is_reviewed_noted_approved_and_its_invoice_credited(client):
    ticket = number(client, "Charged twice")
    assert client.post(f"/v1/staff/tickets/{ticket}/status", json={"status": "in_review"}).json()[
        "status"] == "in_review"
    client.post(f"/v1/staff/tickets/{ticket}/notes",
                json={"text": "Looking into it", "visibility": "customer"})
    client.post(f"/v1/staff/tickets/{ticket}/notes", json={"text": "Retry bug", "visibility": "internal"})
    bad = client.post(f"/v1/staff/tickets/{ticket}/status", json={"status": "closed"})
    assert bad.status_code == 409 and "cannot become" in bad.json()["detail"]
    approved = client.post(f"/v1/staff/tickets/{ticket}/status", json={"status": "resolved", "credit": True})
    assert approved.json()["status"] == "resolved"
    assert approved.json()["events"][-1]["text"].startswith("resolved: Refund approved: inv-sarah-1 partly")
    invoices = client.get("/v1/staff/customers/t_us/sarah").json()["invoices"]
    assert {i["invoice_id"]: i["status"] for i in invoices}["inv-sarah-1"] == "partly credited"
    customer_view = Tickets(connect).get("t_us", "sarah", int(ticket.removeprefix("TKT-")))
    assert [e["text"] for e in customer_view.events if e["kind"] == "note"] == ["Looking into it"]
    other = number(client, "VPN drops")
    refused = client.post(f"/v1/staff/tickets/{other}/status", json={"status": "resolved", "credit": True})
    assert refused.status_code == 409 and "refund" in refused.json()["detail"]   # no invoice to credit


def test_customers_list_everyone_with_their_tickets_and_sign_in(client):
    people = {c["principal_id"]: c for c in client.get("/v1/staff/customers").json()["customers"]}
    assert set(people) == {"sarah", "greta"}
    assert people["sarah"]["online"] and people["sarah"]["company"] == "Demo Co"
    assert not people["greta"]["online"] and people["greta"]["open_tickets"] == 1
    one = client.get("/v1/staff/customers/t_eu/greta").json()
    assert one["account"]["plan"] and len(one["invoices"]) == 3 and len(one["services"]) == 5
    assert client.get("/v1/staff/customers/t_eu/nobody").status_code == 404
    assert client.get("/v1/staff/customers/t_xx/greta").status_code == 404


def test_staff_act_on_any_customers_service_and_it_shows_in_activity(client):
    every = client.get("/v1/staff/services").json()["services"]
    assert {(s["tenant_id"], s["principal_id"]) for s in every} == {("t_us", "sarah"), ("t_eu", "greta")}
    server = next(s for s in every if s["tenant_id"] == "t_eu" and s["name"] == "payments-api-01")
    url = f"/v1/staff/services/t_eu/{server['service_id']}/actions"
    assert client.post(url, json={"action": "restart"}).json()["status"] == "restarting"
    assert client.post(url, json={"action": "restart"}).status_code == 409          # still restarting
    assert client.post(f"/v1/staff/services/t_us/{server['service_id']}/actions",
                       json={"action": "restart"}).status_code == 409              # not in that tenant
    feed = client.get("/v1/staff/activity").json()["activity"]
    assert {"ticket", "service"} <= {e["type"] for e in feed}
    assert next(e for e in feed if e["type"] == "service")["actor"] == "staff:demo"
    assert client.get("/v1/staff/handoffs").json() == {"handoffs": []}
