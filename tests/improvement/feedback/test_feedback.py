"""M6-01: feedback signals (thumbs, the effort survey, reason codes, implicit signals), Jev triage of free
text, the code-versioned failure taxonomy and the weekly review (NFR-31, NFR-32)."""

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

from core.orchestrator.decisions import NUMBERS, Answer, DecisionEngine, JevUnavailable, Masked
from data.migrate import migrate, psql
from hitl.approval import RejectReason
from hitl.handoff import DraftFailure
from improvement import feedback
from improvement.feedback import Event, Feedback, live_metrics, taxonomy, triage, weekly_review
from observability.alerts import Failure
from safety.pii.tokens import Tokenizer

ROOT = Path(__file__).resolve().parents[3]
DB = "test_feedback"
DSN = f"postgresql://agent:change-me@127.0.0.1:5432/{DB}"  # credentials from .env.example


class Jev:
    """category: (node id, confidence); severity: level 0..4."""

    def __init__(self, category=("gate.hallucination", 0.9), severity=2, down=False):
        self.category, self.severity, self.down, self.seen = category, severity, down, []

    def evaluate(self, state, questions):
        self.seen.append((state, questions))
        if self.down:
            raise JevUnavailable("down")
        return {"category": Answer(self.category[0], self.category[1]),
                "severity": Answer(float(self.severity), 0.9, self.severity)}


# The taxonomy -----------------------------------------------------------------------------------------------

def test_the_taxonomy_is_two_levels_with_a_quadrant_and_tier_and_jev_safe_descriptions():
    for node in taxonomy.TREE.values():
        assert node.id == taxonomy.UNCLASSIFIED or node.id.count(".") == 1
        assert node.quadrant in {"KK", "KU", "UK", "UU"} and node.tier in taxonomy.TIER_WEIGHT
        assert not NUMBERS.search(node.description), node.id
        assert not node.postmortem or node.tier == 4
    assert {n.subsystem for n in taxonomy.TREE.values()} >= {"triage", "retrieval", "multi_agent", "tools",
                                                              "safety", "gate"}
    must_postmortem = {"safety.jailbreak", "safety.pii_disclosure", "tools.unapproved_financial_write"}
    assert must_postmortem <= {n.id for n in taxonomy.TREE.values() if n.postmortem}


def test_every_existing_failure_code_lands_on_the_one_tree():
    for codes in (Failure, DraftFailure, RejectReason):
        for code in codes:
            assert taxonomy.node(code.value).id in taxonomy.TREE, code
    assert taxonomy.node("gate.miscalculation").tier == 2
    with pytest.raises(KeyError, match="not in taxonomy"):
        taxonomy.node("vibes.bad")


def test_the_triage_questions_pass_the_decision_engines_number_rule():
    engine = DecisionEngine(Jev())
    assert engine.ask({"feedback": Masked("x")}, {"category": feedback.CATEGORY,
                                                  "severity": feedback.SEVERITY})["category"].value
    assert set(feedback.CATEGORY["criteria"]) == set(taxonomy.TREE)


# Jev triage -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("jev, category, severity, source, page", [
    (Jev(("safety.pii_disclosure", 0.92), 4), "safety.pii_disclosure", 1.0, "jev", True),
    (Jev(("gate.hallucination", 0.9), 3), "gate.hallucination", 0.75, "jev", False),
    (Jev(("gate.hallucination", 0.69), 4), taxonomy.UNCLASSIFIED, 1.0, "residual", True),
    (Jev(down=True), taxonomy.UNCLASSIFIED, None, "residual", False),
])
def test_free_text_gets_a_category_and_a_severity_or_goes_to_the_residuals(jev, category, severity, source,
                                                                           page):
    result = triage(DecisionEngine(jev), Masked("The bot showed me another customer's invoice"))
    got = (result.category, result.severity, result.source, result.page)
    assert got == (category, severity, source, page)


def test_an_answer_outside_the_taxonomy_is_not_taken():
    result = triage(DecisionEngine(Jev(("made.up", 0.99), 4)), Masked("x"))
    assert result == feedback.Triage(taxonomy.UNCLASSIFIED, None, "residual")


# Implicit signals -------------------------------------------------------------------------------------------

def test_a_re_asked_question_is_one_said_again_in_nearly_the_same_words():
    assert feedback.reasked("How do I reset my VPN password?", "how do I reset my vpn password")
    assert not feedback.reasked("How do I reset my VPN password?", "Thanks, that worked!")
    assert feedback.similarity("", "anything") == 0.0


def test_a_case_reopened_within_seven_days_is_a_first_contact_failure():
    resolved = datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert feedback.reopened(resolved, resolved + timedelta(days=7))
    assert not feedback.reopened(resolved, resolved + timedelta(days=7, seconds=1))
    assert not feedback.reopened(resolved, resolved - timedelta(hours=1))
    assert not feedback.reopened(None, resolved)
    assert feedback.abandoned(last_reply_resolved=False, timed_out=True)
    assert not feedback.abandoned(last_reply_resolved=True, timed_out=True)


# The weekly review ------------------------------------------------------------------------------------------

def events(category, n, start=0, severity=0.25):
    return [Event(start + i, "survey", "ces", 3, category, severity, "jev") for i in range(n)]


def test_clusters_rank_by_volume_times_severity_and_the_top_three_get_owners():
    week = (events("infrastructure.timeout", 50) + events("safety.pii_disclosure", 1, 100, severity=1.0)
            + events("gate.hallucination", 4, 200) + events("retrieval.knowledge_gap", 3, 300)
            + events(taxonomy.UNCLASSIFIED, 2, 400))
    review = weekly_review(week, {"safety": "ana", "gate": "ben", "infrastructure": "cy"})
    assert [(c.category, c.priority) for c in review.clusters] == [
        ("safety.pii_disclosure", 100), ("infrastructure.timeout", 50), ("gate.hallucination", 40),
        ("retrieval.knowledge_gap", 30), (taxonomy.UNCLASSIFIED, 20)]
    assert [c.owner for c in review.clusters[:3]] == ["ana", "cy", "ben"]
    assert review.clusters[3].owner is None and review.owners_missing == ()
    assert review.postmortems == (100,) and review.high_severity == (100,) and review.unclassified == 2


def test_a_postmortem_is_for_what_happened_not_for_what_a_specialist_stopped():
    stopped = Event(1, "reason_code", "unauthorized_user", None, "safety.authorization_escape", None,
                    "rejection")
    happened = Event(2, "reason_code", "x", None, "safety.authorization_escape", None, "reason_code")
    review = weekly_review([stopped, happened], {})
    assert review.postmortems == (2,) and review.owners_missing == ("safety",)


def test_a_comment_whose_severity_is_unknown_needs_a_person_not_a_low_rank():
    unknown = Event(7, "survey", "ces", 5, taxonomy.UNCLASSIFIED, None, "residual")
    assert weekly_review([unknown], {}).needs_person == (7,)


def test_live_metrics_give_the_release_gate_its_csat_effort_and_first_contact_resolution():
    week = [Event(1, "thumbs", "up"), Event(2, "thumbs", "up"), Event(3, "thumbs", "down"),
            Event(4, "survey", "ces", 2), Event(5, "survey", "ces", 4), Event(6, "implicit", "reopen"),
            Event(7, "implicit", "reask"), Event(8, "implicit", "abandon"), Event(9, "implicit", "reask")]
    metrics = live_metrics(week, conversations=20, resolved=10)
    assert metrics == {"csat": pytest.approx(2 / 3), "ces": 3.0, "fcr": 0.9, "reask_rate": 0.1,
                       "abandonment_rate": 0.05, "reopen_rate": 0.1}
    assert live_metrics([], 0, 0) == dict.fromkeys(metrics)


# The store --------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def database():
    subprocess.run(["docker", "compose", "-f", "deploy/compose.yaml", "--env-file", ".env.example",
                    "up", "-d", "--wait", "postgres"], cwd=ROOT, check=True, capture_output=True)
    psql("postgres", f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)')
    psql("postgres", f'CREATE DATABASE "{DB}"')
    migrate("main", DB)
    feedback.apply_schema(DB)
    feedback.apply_schema(DB)                                # applied once
    psql(DB, "INSERT INTO tenants (id, name, region) VALUES ('t_us', 'Acme', 'US'), ('t_eu', 'Globex', 'EU')")


def store(jev=None, pages=None):
    def page(tenant, event):
        (pages if pages is not None else []).append((tenant, event))
    return Feedback(lambda: psycopg.connect(DSN, autocommit=True), Tokenizer(b"k" * 32),
                    DecisionEngine(jev or Jev()), page)


WEEK = (datetime.now(timezone.utc) - timedelta(days=1), datetime.now(timezone.utc) + timedelta(days=1))


def test_a_survey_comment_is_tokenized_triaged_stored_and_a_critical_one_pages_on_call():
    pages, jev = [], Jev(("safety.pii_disclosure", 0.95), 4)
    event_id, result = store(jev, pages).survey("t_us", "sarah", "c-1", 5,
                                                "It showed bob@globex.com's invoice to me!!")
    sent = jev.seen[0][0]["feedback"]
    assert "bob@globex.com" not in sent and "<EMAIL_" in sent
    assert result.page and pages[0][0] == "t_us" and pages[0][1]["event_id"] == event_id
    (row,) = psql(DB, f"SELECT comment, category, severity, source, taxonomy, user_hash FROM feedback_events "
                      f"WHERE id = {event_id}")
    assert "bob@globex.com" not in row[0]
    assert row[1:5] == ["safety.pii_disclosure", "1.000", "jev", taxonomy.VERSION]
    assert row[5] != "sarah" and len(row[5]) == 64


def test_a_survey_without_a_comment_asks_jev_nothing_and_a_bad_score_is_refused():
    jev = Jev()
    event_id, result = store(jev).survey("t_us", "sarah", "c-2", 1)
    assert result is None and jev.seen == []
    for score in (0, 6, 2.5, "3"):
        with pytest.raises(ValueError, match="whole number from 1 to 5"):
            store().survey("t_us", "sarah", "c-2", score)


def test_a_comment_that_still_holds_personal_data_is_refused():
    class Leaky:
        def mask(self, text):
            return text, {}
    leaky = Feedback(lambda: psycopg.connect(DSN, autocommit=True), Leaky(), DecisionEngine(Jev()),
                     lambda *a: None)
    with pytest.raises(ValueError, match="still holds personal data"):
        leaky.survey("t_us", "sarah", "c-3", 4, "call me on +1 415 555 0100")


def test_thumbs_reason_codes_and_implicit_signals_are_recorded_and_unknown_ones_refused():
    fb = store()
    fb.thumbs("t_us", "sarah", "c-4", "turn-1", up=False)
    fb.reason_code("t_us", "spec-ana", "c-4", "numeric_miscalculation")
    fb.reason_code("t_us", "spec-ana", "c-4", "unauthorized_user", rejection=True)
    fb.implicit("t_us", "sarah", "c-4", "reask")
    with pytest.raises(ValueError, match="unknown signal"):
        fb.implicit("t_us", "sarah", "c-4", "rage_click")
    with pytest.raises(KeyError):
        fb.reason_code("t_us", "spec-ana", "c-4", "made_up_code")
    kinds = [(e.kind, e.signal, e.category, e.source) for e in fb.events("t_us", *WEEK)]
    assert ("reason_code", "numeric_miscalculation", "gate.miscalculation", "reason_code") in kinds
    assert ("reason_code", "unauthorized_user", "safety.authorization_escape", "rejection") in kinds
    assert ("thumbs", "down", None, None) in kinds and ("implicit", "reask", None, None) in kinds


def test_tenants_see_only_their_own_feedback_and_rows_cannot_be_rewritten():
    store().thumbs("t_eu", "hans", "c-eu", "turn-1", up=True)
    eu, us = store().events("t_eu", *WEEK), store().events("t_us", *WEEK)
    assert {e.id for e in eu}.isdisjoint({e.id for e in us}) and len(eu) == 1
    with psycopg.connect(DSN, autocommit=True) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        with conn.transaction():
            feedback._context(conn, "t_us")
            conn.execute("UPDATE feedback_events SET signal = 'up'")


def test_erasure_removes_every_signal_of_the_user():
    fb = store()
    fb.thumbs("t_us", "erin", "c-5", "turn-1", up=True)
    fb.survey("t_us", "erin", "c-5", 2, "fine")
    assert fb.erase("t_us", "erin") == 2 and fb.erase("t_us", "erin") == 0


def test_the_weekly_review_runs_on_the_stored_week():
    fb = store(Jev(("tools.unapproved_financial_write", 0.9), 4))
    event_id, _ = fb.survey("t_us", "sarah", "c-6", 5, "I was charged twice and nobody approved it")
    review = weekly_review(fb.events("t_us", *WEEK), {"tools": "dee"})
    assert event_id in review.postmortems and event_id in review.high_severity
    financial = next(c for c in review.clusters if c.category == "tools.unapproved_financial_write")
    assert financial.priority >= 100 and financial in review.clusters[:4]   # ties with the other tier-4 rows
