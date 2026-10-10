"""M0-03: forced row-level security, region pinning, append-only audit, tenant settings and separate vault and
key-store databases (NFR-1, NFR-2, NFR-3, AC-11). Runs against the compose Postgres, as the real roles."""

import subprocess
from pathlib import Path

import pytest

from data.migrate import PsqlError, migrate, psql

ROOT = Path(__file__).resolve().parents[2]
MAIN, VAULT, KEYS = "test_main", "test_vault", "test_keys"


@pytest.fixture(scope="module", autouse=True)
def databases():
    subprocess.run(
        ["docker", "compose", "-f", "deploy/compose.yaml", "--env-file", ".env.example",
         "up", "-d", "--wait", "postgres"],
        cwd=ROOT, check=True, capture_output=True,
    )
    for name, kind in ((MAIN, "main"), (VAULT, "vault"), (KEYS, "keys")):
        psql("postgres", f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        psql("postgres", f'CREATE DATABASE "{name}"')
        migrate(kind, name)
    # Fixtures are written as the superuser, which bypasses row-level security.
    psql(MAIN, """
        INSERT INTO tenants (id, name, region) VALUES ('t_us', 'Acme', 'US'), ('t_eu', 'Globex', 'EU');
        INSERT INTO tenant_settings (tenant_id) VALUES ('t_us'), ('t_eu');
        INSERT INTO tool_audit_records (tenant_id, conversation_id, tool, action, outcome)
          VALUES ('t_us', 'c1', 'refund', 'create', 'ok'), ('t_eu', 'c2', 'refund', 'create', 'ok');
        INSERT INTO decision_records (tenant_id, conversation_id, decision, reason)
          VALUES ('t_us', 'c1', 'approve', 'under threshold'), ('t_eu', 'c2', 'escalate', 'over threshold');
        INSERT INTO transcript_legal_archive (tenant_id, conversation_id, transcript)
          VALUES ('t_us', 'c1', '[]'), ('t_eu', 'c2', '[]');
    """)
    psql(VAULT, "INSERT INTO pii_tokens (tenant_id, token, kind, value_enc) "
                "VALUES ('t_us', 'tok1', 'email', '\\x01'), ('t_eu', 'tok1', 'email', '\\x02')")
    psql(KEYS, "INSERT INTO user_keys (tenant_id, user_id, wrapped_dek) "
               "VALUES ('t_us', 'u1', '\\x01'), ('t_eu', 'u2', '\\x02')")


def as_role(database, role, tenant, sql):
    """Run SQL in one transaction as a role, with the tenant context set the way the application sets it."""
    context = f"SET LOCAL app.current_tenant_id = '{tenant}';\n" if tenant is not None else ""
    return psql(database, f"BEGIN;\nSET LOCAL ROLE {role};\n{context}{sql};\nCOMMIT;\n")


def raises(database, role, tenant, sql, message):
    with pytest.raises(PsqlError, match=message):
        as_role(database, role, tenant, sql)


def test_every_tenant_table_enforces_row_level_security():
    query = """
        SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
               (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) > 0
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r'
          AND (c.relname = 'tenants' OR EXISTS (SELECT FROM pg_attribute a
               WHERE a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped))
    """
    tables = {database: psql(database, query) for database in (MAIN, VAULT, KEYS)}
    assert len(tables[MAIN]) == 5 and len(tables[VAULT]) == 1 and len(tables[KEYS]) == 1
    for rows in tables.values():
        for name, enabled, forced, has_policy in rows:
            assert (enabled, forced, has_policy) == ("t", "t", "t"), name


@pytest.mark.parametrize("table", ["tenants", "tenant_settings", "tool_audit_records", "decision_records"])
def test_runtime_reads_only_its_own_tenant(table):
    column = "id" if table == "tenants" else "tenant_id"
    assert as_role(MAIN, "app_runtime", "t_us", f"SELECT DISTINCT {column} FROM {table}") == [["t_us"]]
    assert as_role(MAIN, "app_runtime", "t_eu", f"SELECT DISTINCT {column} FROM {table}") == [["t_eu"]]


def test_no_tenant_context_reads_nothing():
    assert as_role(MAIN, "app_runtime", None, "SELECT * FROM tool_audit_records") == []
    assert as_role(MAIN, "app_runtime", "", "SELECT * FROM tenant_settings") == []


def test_force_applies_to_the_table_owner():
    assert as_role(MAIN, "app_owner", None, "SELECT * FROM tenants") == []
    assert as_role(MAIN, "app_owner", "t_eu", "SELECT id FROM tenants") == [["t_eu"]]


def test_runtime_cannot_write_into_another_tenant():
    raises(MAIN, "app_runtime", "t_us",
           "INSERT INTO decision_records (tenant_id, conversation_id, decision, reason) "
           "VALUES ('t_eu', 'c9', 'approve', 'x')", "row-level security")
    assert as_role(MAIN, "app_runtime", "t_us",
                   "UPDATE tenant_settings SET version = version + 1 "
                   "WHERE tenant_id = 't_eu' RETURNING 1") == []


def test_audit_tables_are_append_only():
    as_role(MAIN, "app_runtime", "t_us",
            "INSERT INTO tool_audit_records (tenant_id, conversation_id, tool, action, outcome) "
            "VALUES ('t_us', 'c3', 'lookup', 'read', 'ok')")
    for table in ("tool_audit_records", "decision_records"):
        raises(MAIN, "app_runtime", "t_us", f"UPDATE {table} SET conversation_id = 'x'", "permission denied")
        raises(MAIN, "app_runtime", "t_us", f"DELETE FROM {table}", "permission denied")
        raises(MAIN, "app_owner", "t_us", f"UPDATE {table} SET conversation_id = 'x'", "append-only")
        raises(MAIN, "app_owner", "t_us", f"DELETE FROM {table}", "append-only")
        raises(MAIN, "app_owner", None, f"TRUNCATE {table}", "append-only")


def test_transcript_archive_is_reachable_only_by_the_legal_role():
    raises(MAIN, "app_runtime", "t_us", "SELECT * FROM transcript_legal_archive", "permission denied")
    assert as_role(MAIN, "legal_archive", "t_us",
                   "SELECT DISTINCT tenant_id FROM transcript_legal_archive") == [["t_us"]]
    raises(MAIN, "legal_archive", "t_us", "UPDATE transcript_legal_archive SET transcript = '[]'",
           "permission denied")
    raises(MAIN, "app_owner", "t_us", "DELETE FROM transcript_legal_archive", "retained until")


def test_tenant_region_is_one_of_the_launch_regions_and_pinned():
    with pytest.raises(PsqlError, match="check constraint"):
        psql(MAIN, "INSERT INTO tenants (id, name, region) VALUES ('t_in', 'Initech', 'IN')")
    with pytest.raises(PsqlError, match="pinned to region US"):
        psql(MAIN, "UPDATE tenants SET region = 'EU' WHERE id = 't_us'")


def test_tenant_settings_defaults_and_optimistic_concurrency():
    assert as_role(MAIN, "app_runtime", "t_us",
                   "SELECT approval_threshold_usd, senior_amount_usd, version FROM tenant_settings") == [
        ["1000.00", "5000.00", "1"]]
    update = ("UPDATE tenant_settings SET approval_threshold_usd = 2000, version = 2 "
              "WHERE tenant_id = 't_us' AND version = 1 RETURNING version")
    assert as_role(MAIN, "app_runtime", "t_us", update) == [["2"]]
    # A second writer still holding version 1 changes nothing; one that skips the version bump is refused.
    assert as_role(MAIN, "app_runtime", "t_us", update) == []
    raises(MAIN, "app_runtime", "t_us", "UPDATE tenant_settings SET monthly_budget_usd = 5",
           "stale tenant settings")
    raises(MAIN, "app_runtime", "t_us",
           "UPDATE tenant_settings SET senior_amount_usd = 10, version = version + 1", "check constraint")


def test_vault_and_key_store_are_separate_databases_with_their_own_roles():
    privileges = psql("postgres", f"""
        SELECT r, d, has_database_privilege(r, d, 'CONNECT')
        FROM unnest(ARRAY['app_runtime', 'legal_archive', 'vault_service', 'key_service']) r,
             unnest(ARRAY['{MAIN}', '{VAULT}', '{KEYS}']) d
    """)
    allowed = {(r, d) for r, d, ok in privileges if ok == "t"}
    assert allowed == {("app_runtime", MAIN), ("legal_archive", MAIN),
                       ("vault_service", VAULT), ("key_service", KEYS)}


def test_vault_and_key_store_isolate_tenants():
    assert as_role(VAULT, "vault_service", "t_us", "SELECT value_enc FROM pii_tokens") == [["\\x01"]]
    assert as_role(KEYS, "key_service", "t_eu", "SELECT user_id FROM user_keys") == [["u2"]]
    assert as_role(KEYS, "key_service", "t_us",
                   "DELETE FROM user_keys WHERE user_id = 'u2' RETURNING 1") == []
    raises(KEYS, "key_service", "t_us", "UPDATE user_keys SET wrapped_dek = '\\x00'", "permission denied")


def test_crypto_shredding_deletes_only_the_users_key():
    shredded = as_role(KEYS, "key_service", "t_us",
                       "DELETE FROM user_keys WHERE user_id = 'u1' RETURNING user_id")
    assert shredded == [["u1"]]
    assert psql(KEYS, "SELECT user_id FROM user_keys") == [["u2"]]


def test_migrations_apply_once():
    for name, kind in ((MAIN, "main"), (VAULT, "vault"), (KEYS, "keys")):
        assert migrate(kind, name) == [], name
