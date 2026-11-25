"""The portal's thumbs loop (built outside Genesis, 2026-11-20): a reply's vote is final and may carry a
masked, triaged comment; the staff console and the CLI report read every rating with its reply; a thumbs
down becomes a masked regression case that a replay asks again and Jev judges."""

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from channels import sessions
from channels.api import turns
from channels.api.app import create_app
from channels.api.envelope import EventType, Principal
from channels.api.staff import staff_router
from core.orchestrator.decisions import Answer, DecisionEngine, JevUnavailable
from data import events
from data.events import EventLog
from data.migrate import migrate, psql
from evals import regressions
from improvement import feedback
from improvement.feedback import Feedback
from memory.lifecycle import inventory
from safety.governance import records
from safety.governance.records import DecisionLog
from safety.pii.tokens import Tokenizer
from scripts.feedback_report import report as print_report

ROOT = Path(__file__).resolve().parents[3]
DB = "test_thumbs"
DSN = f"postgresql://agent:change-me@127.0.0.1:5432/{DB}"  # credentials from .env.example
TOKENS = Tokenizer(b"k" * 32)
WEEK_AGO = datetime.now(timezone.utc) - timedelta(days=7)


def connect():
    return psycopg.connect(DSN, autocommit=True)


class Jev:
    def __init__(self, answers=None, down=False):
        self.answers, self.down, self.seen = answers or {}, down, []

    def evaluate(self, state, questions):
        self.seen.append((state, questions))
        if self.down:
            raise JevUnavailable("down")
        return {qid: self.answers[qid] for qid in questions}


TRIAGE = {"category": Answer("retrieval.knowledge_gap", 0.9), "severity": Answer(2.0, 0.9, 2)}


@pytest.fixture(scope="module")
def principals():
    subprocess.run(["docker", "compose", "-f", "deploy/compose.yaml", "--env-file", ".env.example",
                    "up", "-d", "--wait", "postgres"], cwd=ROOT, check=True, capture_output=True)
    psql("postgres", f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)')
    psql("postgres", f'CREATE DATABASE "{DB}"')
    migrate("main", DB)
    for module in (events, turns, sessions, records, feedback, regressions, feedback):   # applied once
        module.apply_schema(DB)
    psql(DB, "INSERT INTO tenants (id, name, region) VALUES ('t_us', 'Acme', 'US'), ('t_eu', 'Globex', 'EU')")
    with connect() as conn:
        return {who: Principal(tenant_id="t_us", principal_id=who, assurance_tier="T1_VERIFIED",
                               session_id=sessions.start(conn, "t_us", who, "T1_VERIFIED"))
                for who in ("sarah", "bob")}


def api(principals, jev=None, tokenizer=TOKENS, pages=None):
    def authenticate(request):
        principal = principals[request.headers["X-Who"]]
        conversation = request.path_params.get("conversation_id")
        with connect() as conn:  # the ownership check Identity.authenticate makes
            if conversation and not sessions.open_conversation(conn, "t_us", principal.principal_id,
                                                               principal.session_id, conversation):
                raise PermissionError(conversation)
        return principal
    store = Feedback(connect, tokenizer, DecisionEngine(jev or Jev(TRIAGE)),
                     lambda tenant, event: (pages if pages is not None else []).append(event))
    return TestClient(create_app(DSN, authenticate=authenticate, dispatch=lambda turn: None, feedback=store))


def exchange(client, who, conversation, question, reply, turn_id, model="llama-3.3-70b"):
    """A question and its reply as the turn loop leaves them: transcript rows and the decision record."""
    sent = client.post(f"/v1/conversations/{conversation}/turns", json={"content": question},
                       headers={"X-Who": who, "Idempotency-Key": f"k-{turn_id}"})
    assert sent.status_code == 202
    with connect() as conn:
        turns.publish(EventLog(conn), "t_us", conversation, EventType.FINAL,
                      {"role": "assistant", "text": reply, "turn_id": turn_id})
    DecisionLog(connect).record("t_us", conversation, "answered", "Answered from the knowledge base", [],
                                {"steps": {"answer": {"model": model}}}, turn_id=turn_id)


def rate(client, who, conversation, turn_id, signal, comment=None):
    return client.post(f"/v1/conversations/{conversation}/turns/{turn_id}/feedback",
                       json={"signal": signal, "comment": comment}, headers={"X-Who": who})


def test_a_vote_is_final_and_a_comment_is_masked_and_triaged(principals):
    jev, pages = Jev(TRIAGE), []
    client = api(principals, jev, pages=pages)
    exchange(client, "sarah", "f1", "How long is the refund window?", "Refunds take 30 days.", "turn-f1")
    first = rate(client, "sarah", "f1", "turn-f1", "down", "Wrong, it is 14 days. Mail me at sarah@acme.com")
    assert first.status_code == 201 and first.json() == {"signal": "down", "comment": "kept"}
    assert "sarah@acme.com" not in jev.seen[0][0]["feedback"] and pages == []
    assert rate(client, "sarah", "f1", "turn-f1", "up").status_code == 409
    assert len(jev.seen) == 1                                   # the second vote never reached Jev
    (row,) = psql(DB, "SELECT signal, comment, category FROM feedback_events WHERE turn_id = 'turn-f1'")
    assert row[0] == "down" and "sarah@acme.com" not in row[1] and row[2] == "retrieval.knowledge_gap"
    assert client.get("/v1/conversations/f1/feedback", headers={"X-Who": "sarah"}).json() == {
        "votes": {"turn-f1": "down"}}


def test_only_a_reply_in_your_own_conversation_can_be_rated(principals):
    client = api(principals)
    exchange(client, "sarah", "f2", "Where are my invoices?", "Under Billing.", "turn-f2")
    assert rate(client, "sarah", "f2", "turn-nope", "up").status_code == 404
    assert rate(client, "sarah", "f2", "bad id!", "up").status_code == 422
    assert rate(client, "sarah", "f2", "turn-f2", "meh").status_code == 422
    with pytest.raises(PermissionError):
        rate(client, "bob", "f2", "turn-f2", "up")
    assert rate(client, "sarah", "f2", "turn-f2", "up").json() == {"signal": "up", "comment": "none"}


def test_a_comment_left_holding_personal_data_is_dropped_and_the_vote_kept(principals):
    class Leaky:
        def mask(self, text):
            return text, {}
    jev = Jev(TRIAGE)
    client = api(principals, jev, tokenizer=Leaky())
    exchange(client, "sarah", "f3", "Can you call me?", "We do not call.", "turn-f3")
    reply = rate(client, "sarah", "f3", "turn-f3", "down", "call me on +1 415 555 0100")
    assert reply.json() == {"signal": "down", "comment": "dropped"} and jev.seen == []
    assert psql(DB, "SELECT signal, comment FROM feedback_events WHERE turn_id = 'turn-f3'") == [["down", ""]]


def test_jev_down_keeps_the_comment_unclassified_and_a_critical_one_pages(principals):
    client = api(principals, Jev(down=True))
    exchange(client, "sarah", "f4", "Show my invoice", "Here is Globex's invoice.", "turn-f4")
    assert rate(client, "sarah", "f4", "turn-f4", "down", "That is not my invoice").status_code == 201
    category = psql(DB, "SELECT category FROM feedback_events WHERE turn_id = 'turn-f4'")
    assert category == [[feedback.taxonomy.UNCLASSIFIED]]
    pages = []
    critical = Jev({"category": Answer("safety.pii_disclosure", 0.95), "severity": Answer(4.0, 0.9, 4)})
    client = api(principals, critical, pages=pages)
    exchange(client, "sarah", "f5", "My invoice?", "Another customer's invoice.", "turn-f5")
    rate(client, "sarah", "f5", "turn-f5", "down", "I saw someone else's invoice")
    assert pages and pages[0]["category"] == "safety.pii_disclosure"


def test_without_a_feedback_store_the_routes_say_so(principals):
    client = TestClient(create_app(DSN, authenticate=lambda request: principals["sarah"]))
    assert client.post("/v1/conversations/x/turns/t/feedback", json={"signal": "up"}).status_code == 503
    assert client.get("/v1/conversations/x/feedback").json() == {"votes": {}}


def test_ratings_carry_the_reply_the_question_and_the_models_and_the_staff_page_reads_them(principals):
    client = api(principals)
    exchange(client, "bob", "f6", "Is there a free tier?", "Yes, Starter is free.", "turn-f6", "gemini-2.5")
    rate(client, "bob", "f6", "turn-f6", "up")
    rows = feedback.ratings(connect, ("t_us", "t_eu"), WEEK_AGO)
    by_turn = {r["turn_id"]: r for r in rows}
    free = by_turn["turn-f6"]
    assert (free["question"], free["answer"], free["models"]) == (
        "Is there a free tier?", "Yes, Starter is free.", ["gemini-2.5"])
    s = feedback.summary(rows)
    assert s["total"]["up"] == sum(r["signal"] == "up" for r in rows)
    assert s["by_model"]["gemini-2.5"]["up"] == 1
    assert s["down_by_category"]["retrieval.knowledge_gap"] >= 1
    staff = FastAPI()
    staff.include_router(staff_router(connect, tenants=("t_us", "t_eu")))
    page = TestClient(staff).get("/v1/staff/feedback?days=7").json()
    assert page["summary"] == s and len(page["ratings"]) == len(rows)
    lines = []
    print_report(rows, 7, lines.append)
    assert any("Thumbs down" in line for line in lines) and any("gemini-2.5" in line for line in lines)


def test_thumbs_down_export_once_masked_and_replay_sends_unsure_verdicts_to_a_person(principals):
    client = api(principals)
    exchange(client, "sarah", "f7", "I'm sarah@acme.com, what's my plan?", "You are on Growth.", "turn-f7")
    rate(client, "sarah", "f7", "turn-f7", "down", "I am on Starter")
    added, _ = regressions.export(connect, TOKENS, ("t_us",))
    assert added >= 1 and regressions.export(connect, TOKENS, ("t_us",))[0] == 0
    cases = regressions.cases(connect, ("t_us",))
    case = next(c for c in cases if c["question"].endswith("what's my plan?"))
    assert "sarah@acme.com" not in case["question"] and case["models"] == ["llama-3.3-70b"]

    sure = Jev({"verdict": Answer("fixed", 0.92)})
    results = regressions.replay([case], lambda tenant, question: "You are on Starter.", DecisionEngine(sure),
                                 TOKENS)
    assert results[0]["sure"] and results[0]["verdict"] == "fixed"
    assert set(sure.seen[0][0]) == {"question", "old_answer", "new_answer", "complaint"}

    def offline(tenant, question):
        raise TimeoutError("no reply")
    unsure = regressions.replay([case, case], lambda tenant, question: "Maybe Starter?",
                                DecisionEngine(Jev({"verdict": Answer("not_fixed", 0.55)})), TOKENS)
    failed = regressions.replay([case], offline, DecisionEngine(Jev(down=True)), TOKENS)
    assert not unsure[0]["sure"] and failed[0]["error"] == "no reply" and failed[0]["verdict"] is None
    lines = []
    regressions.report(unsure + failed, lines.append)
    assert lines[-1].endswith("0 fixed, 0 not fixed, 3 for you to judge")
    assert any(line.startswith("Old answer:") for line in lines)


def test_erasure_inventory_covers_feedback_and_regression_cases():
    tables = {table for table, _ in inventory.POSTGRES}
    assert {"feedback_events", "regression_cases"} <= tables
