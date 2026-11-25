"""The customer's "How this was answered" view: shape per decision and redaction by assurance tier (FR-34)."""

import json
import re
from datetime import datetime, timezone

from channels.api.trace import customer_trace

AT = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
ANSWERED = {"screening": "review", "masked": True, "intent": "BILLING_QUESTION", "classifier": "jev",
            "stages": ["TRIAGED", "DELIBERATING", "RESOLVED"], "review": "deliver",
            "retrieval": {"query": "refund within <EMAIL_ab12> window", "failed": False,
                          "collections": ["public_knowledge_v1"],
                          "sources": [{"doc_id": "kb-refunds", "title": "How refunds work"},
                                      {"doc_id": "kb-billing", "title": "Billing FAQ"}]},
            "answer": {"outcome": "answered", "retried": True}}


def test_a_signed_in_customer_sees_the_agents_their_tool_calls_and_the_shared_state():
    view = customer_trace("turn-1", "answer", ANSWERED, AT, "T1_VERIFIED")
    assert view["detail"] == "full" and view["final"]["outcome"] == "composed"
    assert [a["name"] for a in view["agents"]] == ["Coordinator", "Knowledge Agent", "Output Reviewer"]
    coordinator, knowledge, reviewer = view["agents"]
    assert [c["name"] for c in coordinator["calls"]] == [
        "Safety screening", "Personal data masking", "Triage"]
    assert coordinator["calls"][2]["args"] == {"intent": "Billing question", "by": "decision engine"}
    lookup = knowledge["calls"][0]
    assert lookup["args"]["index"] == "public_knowledge_v1"
    assert lookup["items"] == ["How refunds work", "Billing FAQ"]
    assert knowledge["outcome"] == "retrieved 2 sources" and knowledge["notes"]
    assert reviewer["outcome"] == "verdict PASS"
    assert view["shared_state"]["retry_count"] == 1 and view["shared_state"]["sources"] == 2


def test_an_anonymous_customer_gets_the_summary_and_outline_only():
    view = customer_trace("turn-1", "answer", ANSWERED, AT, "T0_ANON")
    assert view["detail"] == "summary" and view["shared_state"] == {}
    assert all(a["calls"] == [] and a["notes"] == [] for a in view["agents"])
    assert "<EMAIL_ab12>" not in json.dumps(view)


def test_no_tier_sees_the_screening_band_or_any_score():
    shown = json.dumps(customer_trace("turn-1", "answer", ANSWERED, AT, "T2_SSO")).lower()
    assert "review" not in re.sub(r"output reviewer|reviewer", "", shown)    # the band would map a cut-off
    assert not re.search(r"score|confiden|threshold|probab|jev", shown)


def test_a_blocked_message_stops_at_the_coordinator():
    view = customer_trace("turn-2", "block", {"screening": "block"}, AT, "T1_VERIFIED")
    assert [a["id"] for a in view["agents"]] == ["coordinator"]
    assert view["agents"][0]["status"] == "stopped"
    assert view["agents"][0]["calls"] == [{"name": "Safety screening", "args": {}, "result": "BLOCKED",
                                           "items": []}]


def test_a_record_from_before_steps_were_kept_still_has_a_summary():
    view = customer_trace("turn-3", "escalate", None, AT, "T1_VERIFIED")
    assert view["summary"].startswith("Handed to a specialist") and view["agents"][0]["name"] == "Coordinator"


def test_every_tier_sees_the_models_that_wrote_the_turn():
    steps = {**ANSWERED, "specialists": [{"role": "billing", "outcome": "answered", "claims": 2,
                                          "model": "gemini-3.5-flash-lite"}],
             "answer": {"outcome": "answered", "retried": False, "model": "claude-haiku-5-5"}}
    for tier in ("T0_ANON", "T1_VERIFIED"):
        assert customer_trace("turn-4", "answer", steps, AT, tier)["models"] == [
            "gemini-3.5-flash-lite", "claude-haiku-5-5"]
    knowledge = customer_trace("turn-4", "answer", steps, AT, "T1_VERIFIED")["agents"][1]
    assert knowledge["calls"][-1]["args"] == {"model": "claude-haiku-5-5"}
    assert customer_trace("turn-5", "answer", ANSWERED, AT, "T1_VERIFIED")["models"] == []  # an older record
