"""M1-06: Jev decision client against the documented TypeSafe API, question sets with masked input and a
fallback LLM classifier (FR-5)."""

import json

import httpx
import pytest

from core.orchestrator.decisions import (
    TRIAGE_QUESTIONS,
    Answer,
    DecisionEngine,
    JevClient,
    JevRejected,
    JevUnavailable,
    Masked,
    SecurityViolation,
    mask,
    parse_answer,
)
from core.orchestrator.statechart import build, thread
from core.orchestrator.triage import ASK_CUSTOMER, FALLBACK_MAX_CONFIDENCE, llm_fallback, triage, triage_hook

MESSAGE = {"message": Masked("My last invoice charged me twice")}

# Answer examples copied from the TypeSafe API reference.
DOC_CHOICE = {"type": "choice", "choice": "billing", "confidence": 0.81,
              "probabilities": {"billing": 0.88, "technical": 0.12, "sales": 0.0}}
DOC_SCORE = {"type": "score", "score": 1.05, "confidence": 0.92,
             "legend": {"0": "Calm", "1": "Frustrated", "2": "Very angry"},
             "probabilities": {"0": 0.0, "1": 0.95, "2": 0.05}}
DOC_NOUL = {"type": "noul", "noul": 0.95}


class FakeJev:
    """Answers by question id; records what it was sent; raises when told to be down."""

    def __init__(self, answers=None, down=False):
        self.answers, self.down, self.sent = answers or {}, down, []

    def evaluate(self, state, questions):
        self.sent.append((state, questions))
        if self.down:
            raise JevUnavailable("timeout")
        return {qid: self.answers[qid] for qid in questions if qid in self.answers}


def triage_answers(intent="BILLING_INQUIRY", severity=1, missing=False, confidence=0.95):
    return {"intent": Answer(intent, confidence), "severity": Answer(float(severity), confidence, severity),
            "missing_details": Answer(missing, confidence)}


def fallback_reply(**fields):
    return llm_fallback(lambda prompt: json.dumps({"intent": "BILLING_INQUIRY", "severity": 1,
                                                   "missing_details": False, "confidence": 0.99, **fields}))


def test_documented_answers_parse_into_one_shape():
    assert parse_answer(DOC_CHOICE) == Answer("billing", 0.81)
    assert parse_answer(DOC_SCORE) == Answer(1.05, 0.92, 1)
    split = {"type": "score", "score": 2.8, "confidence": 0.3,
             "probabilities": {"0": 0.0, "1": 0.4, "2": 0.0, "3": 0.0, "4": 0.6}}
    assert parse_answer(split).index == 4  # the most likely level, not the weighted score (2.8)
    assert parse_answer(DOC_NOUL) == Answer(True, 0.95)
    assert parse_answer({"type": "noul", "noul": 0.2}) == Answer(False, 0.8)


def test_triage_maps_typed_answers_onto_acuity():
    billing = triage(DecisionEngine(FakeJev(triage_answers())), MESSAGE)
    assert (billing["acuity"], billing["sop_name"], billing["confidence"]) == (2, "billing_inquiry", 0.95)
    outage = triage(DecisionEngine(FakeJev(triage_answers("TECHNICAL_OUTAGE", severity=4))), MESSAGE)
    assert (outage["acuity"], outage["sop_name"]) == (5, None)


def test_confidence_is_the_intent_confidence_not_severity():
    answers = {**triage_answers(), "severity": Answer(1.0, 0.19, 2)}
    assert triage(DecisionEngine(FakeJev(answers)), MESSAGE)["confidence"] == 0.95


def test_jev_down_uses_the_fallback_classifier_capped_below_sop_confidence():
    result = triage(DecisionEngine(FakeJev(down=True)), MESSAGE, fallback_reply())
    assert result["intent"] == "BILLING_INQUIRY" and result["confidence"] == FALLBACK_MAX_CONFIDENCE
    assert result["rationale"].startswith("fallback")


@pytest.mark.parametrize("fallback", [None, llm_fallback(lambda prompt: "not json"),
                                      fallback_reply(intent="X"), fallback_reply(severity=9)])
def test_without_a_usable_answer_triage_asks_the_customer(fallback):
    assert triage(DecisionEngine(FakeJev(down=True)), MESSAGE, fallback) == ASK_CUSTOMER


def test_an_invalid_jev_answer_counts_as_jev_down():
    wrong = DecisionEngine(FakeJev(triage_answers(intent="REFUND_EVERYTHING")))
    assert triage(wrong, MESSAGE, fallback_reply())["rationale"].startswith("fallback")


def test_only_masked_text_without_personal_data_leaves_for_jev():
    jev = FakeJev(triage_answers())
    engine = DecisionEngine(jev)
    with pytest.raises(SecurityViolation, match="not masked"):
        triage(engine, {"message": "raw text"})
    with pytest.raises(SecurityViolation, match="personal data"):
        triage(engine, {"message": Masked("mail me at sarah@acme.com")})
    raw = "Card 4111 1111 1111 1111, SSN 123-45-6789, sarah@acme.com, +1 415 555 0100"
    triage(engine, {"message": mask(raw)})
    assert jev.sent[-1][0] == {"message": "Card <CARD>, SSN <SSN>, <EMAIL>, <PHONE>"}


def test_guards_need_a_confident_yes_and_fail_safe():
    def guard(answer=None, down=False):
        engine = DecisionEngine(FakeJev({"confirmed": answer} if answer else {}, down=down))
        return engine.guard("confirmed", "Has the customer confirmed the fix?", MESSAGE)

    assert guard(parse_answer({"type": "noul", "noul": 0.95})) is True
    assert guard(parse_answer({"type": "noul", "noul": 0.7})) is False
    assert guard(parse_answer({"type": "noul", "noul": 0.01})) is False
    assert guard(down=True) is False
    assert guard() is False  # no answer at all


def test_numbers_and_money_stay_out_of_question_wording_but_not_option_names():
    engine = DecisionEngine(FakeJev({"big": Answer(True, 1.0), "tool": Answer("read_logs_v2", 0.9)}))
    with pytest.raises(ValueError, match="numbers and money"):
        engine.guard("big", "Is the refund above $1,000?", MESSAGE)
    assert engine.select_tool(["read_logs_v2"], MESSAGE) == "read_logs_v2"


def test_tool_selection_picks_from_the_shortlist_or_nothing():
    def pick(answer=None, down=False, candidates=("status_page", "read_logs")):
        engine = DecisionEngine(FakeJev({"tool": answer} if answer else {}, down=down))
        return engine.select_tool(list(candidates), MESSAGE)

    assert pick(Answer("read_logs", 0.9)) == "read_logs"
    assert pick(Answer("read_logs", 0.5)) is None
    assert pick(Answer("NONE_REQUIRED", 0.99)) is None
    assert pick(Answer("reset_vpn", 0.99)) is None  # not on the shortlist
    assert pick(down=True) is None
    assert pick(candidates=()) is None
    with pytest.raises(ValueError, match="at most 255"):
        pick(candidates=[f"tool_{i}" for i in range(255)])


def client(handler, **options):
    return JevClient("key-1", transport=httpx.MockTransport(handler), backoff=0, **options)


def test_requests_use_the_documented_systemone_shape():
    seen = []

    def handler(request):
        seen.append((request.method, str(request.url), request.headers["Authorization"],
                     json.loads(request.content)))
        usage = {"input_tokens": 3, "output_tokens": 1}
        return httpx.Response(200, json={"model": "jev-1.13.0", "usage": usage,
                                         "answers": {"is_urgent": DOC_NOUL}})

    jev = client(handler)
    answers = jev.evaluate({"message": "hi"}, {"is_urgent": {"type": "noul", "instructions": "Urgent?"}})
    method, url, auth, body = seen[0]
    assert (method, url, auth) == ("POST", "https://api.typesafe.ai/v1/systemone", "Bearer key-1")
    assert body == {"state": {"message": "hi"}, "model": "jev-latest",
                    "questions": {"is_urgent": {"type": "noul", "instructions": "Urgent?"}}}
    assert answers == {"is_urgent": Answer(True, 0.95)}
    assert jev.last == {"model": "jev-1.13.0", "usage": {"input_tokens": 3, "output_tokens": 1}}


def test_triage_questions_follow_the_documented_rules():
    intent = TRIAGE_QUESTIONS["intent"]
    assert intent["type"] == "choice" and isinstance(intent["criteria"], dict)
    assert 2 <= len(TRIAGE_QUESTIONS["severity"]["criteria"]) <= 10
    assert set(TRIAGE_QUESTIONS["missing_details"]["criteria"]) == {"true", "false"}


def test_overload_is_retried_once():
    replies = [httpx.Response(529), httpx.Response(200, json={"model": "m", "usage": {},
                                                             "answers": {"q": DOC_NOUL}})]
    assert client(lambda request: replies.pop(0)).evaluate({}, {"q": {}}) == {"q": Answer(True, 0.95)}
    with pytest.raises(JevUnavailable):
        client(lambda request: httpx.Response(429)).evaluate({}, {})


@pytest.mark.parametrize("status", [400, 401, 413, 415, 422])
def test_refused_requests_are_reported_as_rejected(status):
    with pytest.raises(JevRejected):
        client(lambda request: httpx.Response(status, json={"error": "bad"})).evaluate({}, {})


@pytest.mark.parametrize("reply", [
    lambda request: httpx.Response(503),
    lambda request: httpx.Response(200, json={"oops": 1}),
    lambda request: httpx.Response(200, json={"answers": {"q": {"type": "mystery"}}}),
    lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("slow")),
])
def test_outages_and_malformed_replies_count_as_jev_down(reply):
    with pytest.raises(JevUnavailable) as raised:
        client(reply).evaluate({}, {"q": {}})
    assert not isinstance(raised.value, JevRejected)


def test_a_rejected_request_still_fails_safe():
    jev = client(lambda request: httpx.Response(401, json={"error": "invalid key"}))
    assert DecisionEngine(jev).guard("ok", "Is it fixed?", MESSAGE) is False


def run_case(jev, fallback=None):
    hooks = {"triage": triage_hook(DecisionEngine(jev), fallback),
             "collect": lambda s: {"parameters": {}, "missing": []},
             "execute_sop": lambda s: {"response": "ok"},
             "react_step": lambda s: {"thought": "kb", "outcome": "answer", "answer": "ok"}}
    return build(hooks).invoke({"tenant_id": "t_us", "case_id": "c1", "session_id": "s1",
                                "event": {"type": "USER_MESSAGE", "text": "I was charged twice, a@b.com"}},
                               thread("t_us", "c1"))


def test_typed_answers_drive_statechart_routing():
    assert run_case(FakeJev(triage_answers()))["stage"] == "RESOLVED"
    assert run_case(FakeJev(triage_answers(missing=True)))["stage"] == "AWAITING_CUSTOMER"
    assert run_case(FakeJev(triage_answers(confidence=0.3)))["stage"] == "ESCALATED_HITL"


def test_with_jev_down_a_fallback_guess_never_runs_an_sop_on_its_own():
    state = run_case(FakeJev(down=True), fallback_reply())
    assert "COLLECTING_PARAMS" not in state["path"] and "EXECUTING_SOP" not in state["path"]
    assert state["stage"] == "RESOLVED" and state["triage"]["confidence"] == FALLBACK_MAX_CONFIDENCE


def test_triage_sees_the_whole_thread_not_only_the_last_line():
    jev = FakeJev(triage_answers("PASSWORD_RESET", missing=False))
    triage_hook(DecisionEngine(jev))({"messages": [
        {"role": "user", "content": "Where are my invoices?"},
        {"role": "assistant", "content": "Under Billing & Plan."},                    # answered: not this one
        {"role": "user", "content": "I forgot the password"},
        {"role": "assistant", "content": "Which service?", "kind": "question"},
        {"role": "user", "content": "yes, what is the process"},
    ]})
    sent = jev.sent[-1][0]["message"]
    assert "forgot the password" in sent and "what is the process" in sent and "invoices" not in sent


def test_what_can_be_looked_up_is_not_a_missing_detail():
    """SWEEP-05 (register W42): 'Is the VPN gateway in eu-central having problems?' was asked 'which gateway?'
    before any status lookup. Measured on Jev (KNOWLEDGE-d5cb415b): with this wording both status questions
    and 12 more golden questions go straight to the specialists; vague messages still get asked."""
    lookups = ("service, region or component is having problems", "tickets, subscriptions or invoices")
    wording = TRIAGE_QUESTIONS["missing_details"]["instructions"]
    assert all(phrase in wording for phrase in lookups) and "not missing details" in wording
    prompts = []

    def complete(prompt):
        prompts.append(prompt)
        return '{"intent": "TECHNICAL_OUTAGE", "severity": 1, "missing_details": false, "confidence": 0.9}'
    answers = llm_fallback(complete)({"message": mask("is nimbus down right now?")})
    assert answers["missing_details"].value is False and all(phrase in prompts[0] for phrase in lookups)
