"""The demo Nimbus systems and their read-only tools (AGENT-01, DECISION-ea5b8887 A; FR-20, FR-21, FR-23).

Nimbus is the demo cloud provider the knowledge base describes. Its account, support and status systems are
tables in the main database (tenant row-level security, seeded by deploy/vm/seed_nimbus.py), and each tool is
a plain function behind tools.execution.Executor, so a call gets the executor's session check, on-behalf-of
token, output screening and audit like a call to a real system.

The tools take no arguments (FR-21: nothing a model writes reaches them): each reads only the signed-in
customer's own records. The tool does not trust the call for who that is: it verifies the on-behalf-of token
the executor minted for its system's audience and reads the token's tenant and subject. The audience sets
the tier (channels.identity.tiers.AUDIENCES): service status for anyone, tickets and subscriptions once
verified, the account and invoices only with SSO; below that the executor reports STEP_UP_REQUIRED.
"""

from datetime import date, timedelta

from pydantic import BaseModel

from channels.identity.tiers import Tier
from data.migrate import psql
from tools.execution import System, current
from tools.registry import Registry

SCHEMA_NAME = "nimbus_0001_demo_systems.sql"
TABLES = ("nimbus_accounts", "nimbus_subscriptions", "nimbus_invoices", "nimbus_tickets",
          "nimbus_service_status")
SCHEMA = """
CREATE TABLE nimbus_accounts (
  tenant_id    text NOT NULL REFERENCES tenants (id),
  principal_id text NOT NULL,
  account_id   text NOT NULL,
  company      text NOT NULL,
  plan         text NOT NULL,
  region       text NOT NULL,
  since        date NOT NULL,
  PRIMARY KEY (tenant_id, principal_id)
);
CREATE TABLE nimbus_subscriptions (
  tenant_id       text NOT NULL REFERENCES tenants (id),
  principal_id    text NOT NULL,
  subscription_id text NOT NULL,
  product         text NOT NULL,
  seats           integer NOT NULL,
  status          text NOT NULL,
  renews_on       date NOT NULL,
  PRIMARY KEY (tenant_id, subscription_id)
);
CREATE TABLE nimbus_invoices (
  tenant_id    text NOT NULL REFERENCES tenants (id),
  principal_id text NOT NULL,
  invoice_id   text NOT NULL,
  issued_on    date NOT NULL,
  amount_usd   numeric(14, 2) NOT NULL,
  status       text NOT NULL,
  PRIMARY KEY (tenant_id, invoice_id)
);
CREATE TABLE nimbus_tickets (
  tenant_id    text NOT NULL REFERENCES tenants (id),
  principal_id text NOT NULL,
  ticket_id    text NOT NULL,
  subject      text NOT NULL,
  status       text NOT NULL,
  updated_on   date NOT NULL,
  PRIMARY KEY (tenant_id, ticket_id)
);
CREATE TABLE nimbus_service_status (
  tenant_id  text NOT NULL REFERENCES tenants (id),
  region     text NOT NULL,
  service    text NOT NULL,
  status     text NOT NULL,
  note       text NOT NULL DEFAULT '',
  PRIMARY KEY (tenant_id, region, service)
);
""" + "".join(f"""
ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {t} FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON {t}
  USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());
ALTER TABLE {t} OWNER TO app_owner;
GRANT SELECT, INSERT ON {t} TO app_runtime;
""" for t in TABLES)

SYSTEMS = {  # no hosts: the systems are tables, so a tool has nothing to reach on the network
    "nimbus_status": System("nimbus_status", frozenset(), "obo", "public-read-tools"),
    "nimbus_support": System("nimbus_support", frozenset(), "obo", "status-tools"),
    "nimbus_account": System("nimbus_account", frozenset(), "obo", "account-tools"),
}
STAGES = {"DELIBERATING", "EXECUTING_SOP"}


class NoArguments(BaseModel):
    pass


def apply_schema(database):
    if not psql(database, f"SELECT 1 FROM schema_migrations WHERE name = '{SCHEMA_NAME}'"):
        psql(database, f"BEGIN;\n{SCHEMA}\nINSERT INTO schema_migrations (name) VALUES ('{SCHEMA_NAME}');\n"
                       "COMMIT;\n")


def _rows(connect, tenant_id, sql, params):
    with connect() as conn, conn.transaction():
        conn.execute("SET LOCAL ROLE app_runtime")
        conn.execute("SELECT set_config('app.current_tenant_id', %s, true)", (tenant_id,))
        cursor = conn.execute(sql, params)
        names = [c.name for c in cursor.description]
        return [{n: (str(v) if not isinstance(v, int | str) else v) for n, v in zip(names, row, strict=True)}
                for row in cursor.fetchall()]


def catalog(connect, identity):
    """The production tool registry, frozen. connect(): autocommit connection to the main database;
    identity: channels.identity Identity, which verifies the on-behalf-of token a tool receives."""
    registry = Registry()

    def tool(tool_id, description, min_tier, sql, key):
        audience = SYSTEMS[tool_id.split(".", 1)[0]].audience

        def run(_):
            claims = identity.verify_obo(current().credential, audience)
            return {key: _rows(connect, claims["tid"], sql, {"me": claims["sub"]})}
        registry.tool(tool_id, args=NoArguments, description=description, risk="read_only", stages=STAGES,
                      min_tier=min_tier, author="claude")(run)  # reviewed_by: set when a person reviews it

    tool("nimbus_status.service_status", "Current status of the Nimbus services in every region",
         Tier.T0_ANON, "SELECT region, service, status, note FROM nimbus_service_status "
                       "ORDER BY region, service", "services")
    tool("nimbus_support.my_tickets", "The customer's own support tickets and their status",
         Tier.T1_VERIFIED, "SELECT ticket_id, subject, status, updated_on FROM nimbus_tickets "
                           "WHERE principal_id = %(me)s ORDER BY updated_on DESC", "tickets")
    tool("nimbus_support.my_subscriptions", "The customer's own subscriptions, seats and renewal dates",
         Tier.T1_VERIFIED, "SELECT subscription_id, product, seats, status, renews_on "
                           "FROM nimbus_subscriptions WHERE principal_id = %(me)s ORDER BY product",
         "subscriptions")
    tool("nimbus_account.my_account", "The customer's own account: company, plan and home region",
         Tier.T2_SSO, "SELECT account_id, company, plan, region, since FROM nimbus_accounts "
                      "WHERE principal_id = %(me)s", "account")
    tool("nimbus_account.my_invoices", "The customer's own invoices with their amounts and payment status",
         Tier.T2_SSO, "SELECT invoice_id, issued_on, amount_usd, status FROM nimbus_invoices "
                      "WHERE principal_id = %(me)s ORDER BY issued_on DESC", "invoices")
    return registry.freeze()


def seed(connect, tenant_id, principal_id, company="Demo Co", today=None):
    """Demo rows for one customer, plus the tenant's service status; running it again changes nothing."""
    today = today or date.today()
    short = principal_id.split("@", 1)[0][:12]
    rows = {
        "nimbus_accounts": [(principal_id, f"acct-{short}", company, "Business", "eu-central",
                             today - timedelta(days=400))],
        "nimbus_subscriptions": [(principal_id, f"sub-{short}-1", "Nimbus Compute", 25, "active",
                                  today + timedelta(days=45)),
                                 (principal_id, f"sub-{short}-2", "Nimbus Managed Postgres", 5, "active",
                                  today + timedelta(days=45))],
        "nimbus_invoices": [(principal_id, f"inv-{short}-{n}", today - timedelta(days=30 * n), amount, status)
                            for n, amount, status in ((1, "1840.00", "open"), (2, "1840.00", "paid"),
                                                      (3, "1625.50", "paid"))],
        "nimbus_tickets": [(principal_id, f"tkt-{short}-1", "VPN tunnel drops every few hours", "open",
                            today - timedelta(days=2)),
                           (principal_id, f"tkt-{short}-2", "Add two seats to Compute", "resolved",
                            today - timedelta(days=20))],
        "nimbus_service_status": [("eu-central", "compute", "operational", ""),
                                  ("eu-central", "managed-postgres", "operational", ""),
                                  ("eu-central", "vpn-gateway", "degraded",
                                   "intermittent tunnel resets under investigation"),
                                  ("us-east", "compute", "operational", ""),
                                  ("us-east", "managed-postgres", "operational", ""),
                                  ("us-east", "vpn-gateway", "operational", "")],
    }
    columns = {"nimbus_accounts": "principal_id, account_id, company, plan, region, since",
               "nimbus_subscriptions": "principal_id, subscription_id, product, seats, status, renews_on",
               "nimbus_invoices": "principal_id, invoice_id, issued_on, amount_usd, status",
               "nimbus_tickets": "principal_id, ticket_id, subject, status, updated_on",
               "nimbus_service_status": "region, service, status, note"}
    with connect() as conn, conn.transaction():
        conn.execute("SET LOCAL ROLE app_runtime")
        conn.execute("SELECT set_config('app.current_tenant_id', %s, true)", (tenant_id,))
        for table, values in rows.items():
            marks = ", ".join(["%s"] * (len(values[0]) + 1))
            for row in values:
                conn.execute(f"INSERT INTO {table} (tenant_id, {columns[table]}) VALUES ({marks}) "
                             "ON CONFLICT DO NOTHING", (tenant_id, *row))
