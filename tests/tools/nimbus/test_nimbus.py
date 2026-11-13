"""AGENT-01: the demo Nimbus tools through the real executor, identity and audit log on the compose Postgres:
each tool reads only the signed-in customer's rows in their own tenant, the tier comes from the system's
audience, and every call is in the tool audit log, or returns nothing (FR-20, FR-21, FR-23, FR-24, AC-11)."""

import subprocess
from pathlib import Path

import psycopg
import pytest

from channels import sessions
from channels.identity.service import Identity
from data.migrate import migrate, psql
from safety.screening.pipeline import Screener
from safety.screening.scorers import pattern_injection
from tools import audit as audit_module
from tools import nimbus
from tools.audit import AuditLog
from tools.execution import Executor, ToolFailure
from tools.execution.calls import ToolCall
from tools.registry import Risk

ROOT = Path(__file__).resolve().parents[3]
DB = "test_nimbus"
DSN = f"postgresql://agent:change-me@127.0.0.1:5432/{DB}"  # credentials from .env.example


def connect():
    return psycopg.connect(DSN, autocommit=True)


@pytest.fixture(scope="module")
def stack():
    subprocess.run(["docker", "compose", "-f", "deploy/compose.yaml", "--env-file", ".env.example", "up",
                    "-d", "--wait", "postgres"], cwd=ROOT, check=True, capture_output=True)
    psql("postgres", f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)')
    psql("postgres", f'CREATE DATABASE "{DB}"')
    migrate("main", DB)
    for module in (sessions, audit_module, nimbus):
        module.apply_schema(DB)
    psql(DB, "INSERT INTO tenants (id, name, region) VALUES ('t_us', 'Acme', 'US'), ('t_eu', 'G', 'EU');")
    for tenant, principal, company in (("t_us", "sarah", "Acme"), ("t_us", "bob", "Bobco"),
                                       ("t_eu", "sarah", "Globex")):
        nimbus.seed(connect, tenant, principal, company=company)
    identity = Identity(connect)
    return identity, nimbus.catalog(connect, identity)


class AuditDown(AuditLog):
    def record(self, **fields):
        raise psycopg.OperationalError("audit store unreachable")


def run(stack, tool_id, principal="sarah", tier="T2_SSO", tenant="t_us", audit=None):
    """One call through the executor on a fresh live session; (result or ToolFailure, the call's audit)."""
    identity, registry = stack
    audit_log = AuditLog(connect, lambda: None)
    executor = Executor(registry, nimbus.SYSTEMS, Screener(lambda text: (0.0, []), pattern_injection),
                        identity=identity, audit=audit or audit_log)
    with connect() as conn:
        session = sessions.start(conn, tenant, principal, tier)
    case_id = f"case-{session}"
    try:
        outcome = executor.run(ToolCall(tool_call_id="call-1", tool_id=tool_id, arguments={},
                                        tenant_id=tenant, principal_id=principal, case_id=case_id,
                                        checkpoint_id="cp-1", session_id=session))
    except ToolFailure as failure:
        outcome = failure
    return outcome, audit_log.read(tenant, case_id)


def test_every_catalog_tool_reads_only_and_takes_no_arguments(stack):
    tools = stack[1].tools
    assert set(tools) == {"nimbus_status.service_status", "nimbus_support.my_tickets",
                          "nimbus_support.my_subscriptions", "nimbus_account.my_account",
                          "nimbus_account.my_invoices"}
    for tool in tools.values():
        assert tool.risk is Risk.READ_ONLY and not tool.args.model_fields, tool.tool_id
        assert tool.tool_id.split(".")[0] in nimbus.SYSTEMS


@pytest.mark.parametrize("tool_id, key", [("nimbus_support.my_tickets", "tickets"),
                                          ("nimbus_support.my_subscriptions", "subscriptions"),
                                          ("nimbus_account.my_account", "account"),
                                          ("nimbus_account.my_invoices", "invoices")])
def test_a_tool_returns_only_the_callers_rows_in_their_tenant_and_the_call_is_audited(stack, key, tool_id):
    result, audited = run(stack, tool_id)
    rows = result.data[key]
    assert rows and all("-sarah-" in str(row) or row.get("account_id") == "acct-sarah" for row in rows)
    assert "bob" not in result.content and "Globex" not in result.content
    assert [(r["tool"], r["outcome"], r["user_id"], r["risk_class"], r["credential_basis"])
            for r in audited] == [(tool_id, "ok", "sarah", "read_only", "obo")]


def test_the_same_principal_in_another_tenant_sees_that_tenants_rows(stack):
    result, _ = run(stack, "nimbus_account.my_account", tenant="t_eu")
    assert [row["company"] for row in result.data["account"]] == ["Globex"]


def test_service_status_is_open_to_an_anonymous_session(stack):
    result, audited = run(stack, "nimbus_status.service_status", principal="anon:1", tier="T0_ANON")
    assert {"region": "eu-central", "service": "vpn-gateway", "status": "degraded",
            "note": "intermittent tunnel resets under investigation"} in result.data["services"]
    assert [r["outcome"] for r in audited] == ["ok"]


@pytest.mark.parametrize("tool_id, tier", [("nimbus_support.my_tickets", "T0_ANON"),
                                           ("nimbus_account.my_invoices", "T1_VERIFIED")])
def test_below_the_audiences_tier_the_call_asks_for_step_up_and_is_audited_as_failed(stack, tool_id, tier):
    failure, audited = run(stack, tool_id, tier=tier)
    assert isinstance(failure, ToolFailure) and failure.code == "STEP_UP_REQUIRED"
    assert [(r["outcome"], r["error_code"]) for r in audited] == [("failed", "STEP_UP_REQUIRED")]


def test_a_call_whose_audit_cannot_be_written_returns_nothing(stack):
    failure, audited = run(stack, "nimbus_account.my_invoices", audit=AuditDown(connect, lambda: None))
    assert isinstance(failure, ToolFailure) and failure.code == "AUDIT_UNAVAILABLE" and audited == []


def test_seeding_again_changes_nothing(stack):
    def counts():
        return [psql(DB, f"SELECT count(*) FROM {table}")[0][0] for table in nimbus.TABLES]
    before = counts()
    nimbus.seed(connect, "t_us", "sarah", company="Acme")
    assert counts() == before
