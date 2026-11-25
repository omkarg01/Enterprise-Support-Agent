"""M0-05: 7-day outbound event log with Last-Event-ID replay and transcript rebuild (NFR-4, DP-D7)."""

import subprocess
from pathlib import Path

import psycopg
import pytest

from data.events import EventLog, apply_schema, rebuild_from_transcript
from data.migrate import migrate, psql

ROOT = Path(__file__).resolve().parents[2]
DB = "test_events"
DSN = f"postgresql://agent:change-me@127.0.0.1:5432/{DB}"  # credentials from .env.example


@pytest.fixture(scope="module")
def log():
    subprocess.run(["docker", "compose", "-f", "deploy/compose.yaml", "--env-file", ".env.example",
                    "up", "-d", "--wait", "postgres"], cwd=ROOT, check=True, capture_output=True)
    psql("postgres", f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)')
    psql("postgres", f'CREATE DATABASE "{DB}"')
    migrate("main", DB)
    apply_schema(DB)
    psql(DB, "INSERT INTO tenants (id, name, region) VALUES ('t_us', 'Acme', 'US'), ('t_eu', 'Globex', 'EU')")
    with psycopg.connect(DSN, autocommit=True) as conn:
        yield EventLog(conn)


def backdated(tenant, conversation, days):
    """Insert an event as the superuser, dated in the past, and return its id."""
    [[event_id]] = psql(DB, "INSERT INTO outbound_event_log "
                            "(tenant_id, conversation_id, event_type, created_at) "
                            f"VALUES ('{tenant}', '{conversation}', 'token', now() - interval '{days} days') "
                            "RETURNING id")
    return event_id


def test_reconnect_replays_every_later_event_of_the_conversation_in_order(log):
    first = log.append("t_us", "c1", "status", {"text": "checking"})
    log.append("t_us", "c2", "token", {"text": "other conversation"})
    second = log.append("t_us", "c1", "token", {"text": "Hello"})
    third = log.append("t_us", "c1", "done", {})
    assert log.replay("t_us", "c1", first) == [
        (second, "token", {"text": "Hello"}), (third, "done", {})]
    assert log.replay("t_us", "c1", third) == []


def test_another_tenant_cannot_replay_the_conversation(log):
    event_id = log.append("t_us", "c3", "token", {"text": "private"})
    log.append("t_us", "c3", "done", {})
    assert log.replay("t_eu", "c3", event_id) is None


def test_last_event_id_past_the_window_falls_back_to_the_transcript(log):
    old = backdated("t_us", "c4", days=8)
    assert log.replay("t_us", "c4", old) is None
    transcript = [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello"}]
    assert rebuild_from_transcript(transcript) == [
        (None, "stream_expired", {}), (None, "message", transcript[0]), (None, "message", transcript[1])]


@pytest.mark.parametrize("last_event_id", [None, "", "not-a-number", "999999999"])
def test_unknown_last_event_id_falls_back(log, last_event_id):
    assert log.replay("t_us", "c1", last_event_id) is None


def test_purge_removes_expired_events_of_every_tenant_and_keeps_the_window(log):
    backdated("t_us", "c5", days=8)
    backdated("t_eu", "c6", days=30)
    fresh = backdated("t_eu", "c6", days=6)
    assert log.purge_expired() >= 2
    expired = "SELECT count(*) FROM outbound_event_log WHERE created_at < now() - interval '7 days'"
    assert psql(DB, expired) == [["0"]]
    assert psql(DB, f"SELECT count(*) FROM outbound_event_log WHERE id = {fresh}") == [["1"]]


def test_runtime_cannot_delete_or_change_events_inside_the_window(log):
    event_id = log.append("t_us", "c7", "token", {})
    sql = (f"BEGIN; SET LOCAL ROLE app_runtime; SET LOCAL app.current_tenant_id = 't_us';\n"
           f"DELETE FROM outbound_event_log WHERE id = {event_id} RETURNING id;\nCOMMIT;")
    assert psql(DB, sql) == []
    with pytest.raises(Exception, match="permission denied"):
        psql(DB, "BEGIN; SET LOCAL ROLE app_runtime; SET LOCAL app.current_tenant_id = 't_us';\n"
                 "UPDATE outbound_event_log SET event_type = 'x';\nCOMMIT;")


def test_event_log_has_forced_row_level_security_and_applies_once(log):
    assert psql(DB, "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE relname = 'outbound_event_log'") == [["t", "t"]]
    apply_schema(DB)
    assert psql(DB, "SELECT count(*) FROM schema_migrations WHERE name LIKE 'events_%'") == [["3"]]


def test_event_log_needs_an_autocommit_connection():
    with psycopg.connect(DSN) as conn, pytest.raises(ValueError, match="autocommit"):
        EventLog(conn)
