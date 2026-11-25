"""The portal's History list (built outside the spec, 2026-11-17): GET /v1/conversations lists the caller's own
conversations titled by their first message, and a stream asked from Last-Event-ID 0 sends the transcript."""

import json
import subprocess
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from channels import sessions
from channels.api import turns
from channels.api.app import create_app
from channels.api.envelope import Principal, TurnAction, TurnRequest
from data import events
from data.events import EventLog
from data.migrate import migrate, psql

ROOT = Path(__file__).resolve().parents[2]
DB = "test_history_api"
DSN = f"postgresql://agent:change-me@127.0.0.1:5432/{DB}"  # credentials from .env.example


def connect():
    return psycopg.connect(DSN, autocommit=True)


@pytest.fixture(scope="module")
def principals():
    subprocess.run(["docker", "compose", "-f", "deploy/compose.yaml", "--env-file", ".env.example",
                    "up", "-d", "--wait", "postgres"], cwd=ROOT, check=True, capture_output=True)
    psql("postgres", f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)')
    psql("postgres", f'CREATE DATABASE "{DB}"')
    migrate("main", DB)
    for module in (events, turns, sessions):
        module.apply_schema(DB)
    psql(DB, "INSERT INTO tenants (id, name, region) VALUES ('t_us', 'Acme', 'US')")
    with connect() as conn:
        return {who: Principal(tenant_id="t_us", principal_id=who, assurance_tier="T1_VERIFIED",
                               session_id=sessions.start(conn, "t_us", who, "T1_VERIFIED"))
                for who in ("sarah", "bob")}


@pytest.fixture
def client(principals):
    def authenticate(request):
        principal = principals[request.headers["X-Who"]]
        conversation = request.path_params.get("conversation_id")
        with connect() as conn:  # the ownership check Identity.authenticate makes
            if conversation and not sessions.open_conversation(conn, "t_us", principal.principal_id,
                                                               principal.session_id, conversation):
                raise PermissionError(conversation)
        return principal
    return TestClient(create_app(DSN, authenticate=authenticate, dispatch=lambda turn: None, stream_seconds=0.3))


def say(client, who, conversation, text, key):
    reply = client.post(f"/v1/conversations/{conversation}/turns", json={"content": text},
                        headers={"X-Who": who, "Idempotency-Key": key})
    assert reply.status_code == 202


def answer(conversation, event_type, text):
    with connect() as conn:
        turns.publish(EventLog(conn), "t_us", conversation, event_type, {"role": "assistant", "text": text})


def test_history_lists_own_conversations_titled_by_the_first_message_and_replays_them(client):
    say(client, "sarah", "h1", "How do refunds work for annual plans?", "k1")
    say(client, "sarah", "h1", "How do refunds work for annual plans?", "k1")  # a retry is one message
    answer("h1", "final", "Refunds are prorated.")
    say(client, "sarah", "h2", "Reset my password", "k1")
    answer("h2", "handoff", "A specialist will continue here.")
    say(client, "bob", "h3", "Bob's question", "k1")
    client.post("/v1/conversations/h4/turns", json={"content": "x"}, headers={"X-Who": "sarah"})  # no key: 400

    listed = client.get("/v1/conversations", headers={"X-Who": "sarah"}).json()["conversations"]
    assert [(c["id"], c["title"]) for c in listed] == [
        ("h2", "Reset my password"), ("h1", "How do refunds work for annual plans?")]

    with client.stream("GET", "/v1/conversations/h1/events",
                       headers={"X-Who": "sarah", "Last-Event-ID": "0"}) as reply:
        frames = [json.loads(line.removeprefix("data: ")) for line in reply.iter_lines()
                  if line.startswith("data: ")]
    assert all(frame.pop("at", "") != "" for frame in frames[1:])  # when it was said, for the bubble's clock
    assert frames == [{"code": "stream_expired"},
                      {"history": True, "role": "user", "text": "How do refunds work for annual plans?"},
                      {"history": True, "role": "assistant", "text": "Refunds are prorated."}]

    with connect() as conn:
        assert [{k: v for k, v in m.items() if k != "at"} for m in EventLog(conn).transcript("t_us", "h2")] == [
            {"role": "user", "text": "Reset my password"},
            {"role": "system", "text": "A specialist will continue here."}]


def test_a_confirm_button_turn_is_not_kept_as_something_the_customer_typed(principals):
    with connect() as conn:
        turns.submit_turn(conn, principals["sarah"], "h5", "k1", TurnRequest(content="Raise ticket TKT-00001"),
                          lambda turn: None, TurnAction(type="confirm_ticket", proposal="TKT-00001"))
        assert EventLog(conn).transcript("t_us", "h5") == []


def test_a_reopened_reply_keeps_its_turn_and_sources(client):
    say(client, "sarah", "h6", "How do refunds work?", "k1")
    cited = {"doc_id": "kb-refunds", "title": "How refunds work", "excerpt": "Within 30 days."}
    with connect() as conn:
        log = EventLog(conn)
        turns.publish(log, "t_us", "h6", "citation", {"turn_id": "turn-9", **cited})
        turns.publish(log, "t_us", "h6", "citation", {"turn_id": "turn-8", "doc_id": "kb-other"})
        turns.publish(log, "t_us", "h6", "final",
                      {"turn_id": "turn-9", "role": "assistant", "text": "30 days."})

    with client.stream("GET", "/v1/conversations/h6/events",
                       headers={"X-Who": "sarah", "Last-Event-ID": "0"}) as reply:
        frames = [json.loads(line.removeprefix("data: ")) for line in reply.iter_lines()
                  if line.startswith("data: ")]
    assert {k: v for k, v in frames[-1].items() if k != "at"} == {
        "history": True, "role": "assistant", "text": "30 days.", "turn_id": "turn-9", "citations": [cited]}
