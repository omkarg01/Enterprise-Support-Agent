"""M3-03: numeric disagreements go to a person in code, other conflicts are scored by Jev against the
evidence, and the coordinator sends one reply that does not redirect and claims nothing unverified (FR-29 /
MA-ADP-05)."""

import json
from decimal import Decimal

import pytest

from agents.contracts import ExecutedAction
from agents.merge import REDIRECT, Claim, claims_from, merge
from core.orchestrator.decisions import Answer, DecisionEngine, JevUnavailable, SecurityViolation

EVIDENCE = {"ops.uptime": "EU primary cluster uptime for the quarter: 99.98 percent",
            "billing.credit_memos": "Credit memo CM-7 applied for the March incident",
            "kb.sla": "Contracted uptime is 99.9 percent per quarter"}


class Jev:
    """support: {claim text: level 1..5}; redirect: (what the reply asks the customer to do, confidence)."""

    def __init__(self, support=None, redirect=("self_service", 0.95), down=False):
        self.support, self.redirect, self.down, self.seen = support or {}, redirect, down, []

    def evaluate(self, state, questions):
        self.seen.append((state, questions))
        if self.down:
            raise JevUnavailable("down")
        answers = {}
        for qid in questions:
            if qid.startswith("support_"):
                level = self.support[state[f"claim_{qid.removeprefix('support_')}"]]
                answers[qid] = Answer(float(level - 1), 0.9, level - 1)
            elif qid == "redirect":
                answers[qid] = Answer(*self.redirect)
        return answers


def result(role, *claims, status="completed"):
    return {"role": role, "status": status, "findings": json.dumps(list(claims)), "evidence": [], "steps": 2}


def claim(topic, text, evidence, value=None, unit=None):
    return {"topic": topic, "text": text, "evidence": [evidence], **({"value": str(value), "unit": unit}
                                                                     if value is not None else {})}


UPTIME = claim("eu_uptime_q2", "EU database uptime was 99.98 percent", "ops.uptime", "99.98", "percent")
CREDITS = claim("credits_q2", "One credit memo, CM-7, was applied", "billing.credit_memos")


class Drafts:
    def __init__(self, text="Our technical review found EU uptime of 99.98 percent; billing applied CM-7."):
        self.text, self.calls = text, []

    def __call__(self, claims):
        self.calls.append(claims)
        return self.text


def run(results, jev=None, draft=None, verified=()):
    jev, draft = jev or Jev(), draft or Drafts()
    return merge(DecisionEngine(jev), results, EVIDENCE, draft, verified), jev, draft


# Claims -----------------------------------------------------------------------------------------------------

def test_claims_are_typed_and_labelled_with_the_role_that_returned_them():
    (got,) = claims_from(result("technical", {**UPTIME, "role": "billing"}))
    assert got.role == "technical" and got.value == Decimal("99.98") and got.unit == "percent"


@pytest.mark.parametrize("bad", [
    {"status": "blocked", "findings": json.dumps([UPTIME])},
    {"findings": "uptime looked fine"},                                    # not typed
    {"findings": json.dumps([{**UPTIME, "evidence": []}])},               # ungrounded
    {"findings": json.dumps([{**UPTIME, "value": "NaN"}])},
    {"findings": json.dumps([{**UPTIME, "customer_reply": "All good!"}])},
    {"findings": json.dumps({"topic": "x"})},
])
def test_untyped_ungrounded_or_blocked_findings_are_not_used(bad):
    assert claims_from({"role": "technical", "status": "completed", **bad}) == []


def test_nothing_usable_goes_to_a_person():
    outcome, jev, draft = run([result("technical", status="blocked")])
    assert (outcome.kind, outcome.reason) == ("human", "NOTHING_TO_SAY")
    assert draft.calls == [] and jev.seen == []


# Numbers: code only -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("other, conflict", [
    ("99.98", False), ("99.984", False),               # within the percent tolerance
    ("99.90", True), ("99.986", True),
])
def test_numbers_that_disagree_go_to_a_person_with_both_values_and_jev_never_judges_them(other, conflict):
    billing = claim("eu_uptime_q2", f"Credits assume {other} percent uptime", "billing.credit_memos", other,
                    "percent")
    outcome, jev, draft = run([result("technical", UPTIME), result("billing", billing)])
    if conflict:
        assert (outcome.kind, outcome.reason) == ("human", "NUMERIC_DISCREPANCY")
        assert outcome.detail["values"] == {"eu_uptime_q2": {"technical": "99.98 percent",
                                                             "billing": f"{other} percent"}}
        assert jev.seen == [] and draft.calls == []
    else:
        assert outcome.kind == "reply"
        assert not any("claim_a" in str(state) for state, _ in jev.seen)  # agreeing numbers are not scored


def test_two_readings_of_one_customer_record_keep_the_leads_and_say_which_topic_it_settled():
    lead = claim("running_services", "Three of your services are running", "nimbus.my_services", "3",
                 "services")
    other = claim("running_services", "Four services are up", "nimbus.my_services", "4", "services")
    outcome = merge(DecisionEngine(Jev()), [result("account_ops", lead), result("technical", other)],
                    {**EVIDENCE, "nimbus.my_services": "five services"}, Drafts(), lead="account_ops",
                    records={"nimbus.my_services": "five services"})
    assert outcome.kind == "reply" and [c.text for c in outcome.accepted] == [lead["text"]]
    assert outcome.detail["lead_kept"] == ["running_services"]


def test_the_lead_keeps_nothing_when_the_claims_cite_an_article_or_the_lead_said_nothing_on_it():
    lead = claim("eu_uptime_q2", "Uptime was 99.90 percent", "kb.sla", "99.90", "percent")
    outcome = merge(DecisionEngine(Jev()), [result("technical", UPTIME), result("billing", lead)], EVIDENCE,
                    Drafts(), lead="billing", records={"ops.uptime": "x"})
    assert outcome.reason == "NUMERIC_DISCREPANCY" and "lead_kept" not in outcome.detail   # no shared record
    other = claim("eu_uptime_q2", "Uptime was 99.90 percent", "ops.uptime", "99.90", "percent")
    outcome = merge(DecisionEngine(Jev()), [result("technical", UPTIME), result("billing", other)], EVIDENCE,
                    Drafts(), lead="account_ops", records={"ops.uptime": "x"})
    assert outcome.reason == "NUMERIC_DISCREPANCY"                                    # the lead is not in it
    assert outcome.detail["values"] == {"eu_uptime_q2": {"technical": "99.98 percent",
                                                         "billing": "99.90 percent"}}


@pytest.mark.parametrize("value, unit", [("26", "minutes"), ("99.98", "permille"), ("99.98", None)])
def test_the_same_figure_in_different_units_is_a_discrepancy(value, unit):
    other = claim("eu_uptime_q2", "Uptime as billing measured it", "ops.uptime", value, unit)
    outcome, _, _ = run([result("technical", UPTIME), result("billing", other)])
    assert outcome.reason == "NUMERIC_DISCREPANCY"


# Other disagreements: Jev against the evidence --------------------------------------------------------------

A = claim("root_cause", "A database failover caused the outage", "ops.uptime")
B = claim("root_cause", "A billing job caused the outage", "billing.credit_memos")


@pytest.mark.parametrize("a, b, outcome_kind, winner", [
    (5, 1, "reply", "A database failover caused the outage"),
    (2, 4, "reply", "A billing job caused the outage"),
    (4, 3, "human", None), (3, 3, "human", None), (5, 5, "human", None), (2, 2, "human", None),
])
def test_a_conflict_is_settled_only_by_a_clear_evidence_margin(a, b, outcome_kind, winner):
    jev = Jev(support={A["text"]: a, B["text"]: b})
    outcome, _, draft = run([result("technical", A), result("billing", B)], jev)
    assert outcome.kind == outcome_kind
    if winner:
        assert [c.text for c in draft.calls[0]] == [winner]
    else:
        assert outcome.reason == "EVIDENCE_AMBIGUITY" and draft.calls == []
        assert outcome.detail["claims"] == {"technical": (A["text"], a), "billing": (B["text"], b)}
    state, questions = jev.seen[0]
    assert state["evidence_a"] == EVIDENCE["ops.uptime"]                 # each claim with its own evidence
    assert state["evidence_b"] == EVIDENCE["billing.credit_memos"]


@pytest.mark.parametrize("old, new, record", [
    (B, A, None),                                                         # text: no Jev scoring needed
    (claim("eu_uptime_q2", "Archive: 99.90 percent", "billing.credit_memos", "99.90", "percent"), UPTIME, None),
    (B, A, claim("root_cause", "Your ticket says a billing job", "tool.record")),  # undated: kept
])
def test_the_newest_article_wins_a_topic_before_any_conflict_check(old, new, record):
    dated = {"ops.uptime": "2026-09-15", "billing.credit_memos": "2024-11-10"}
    results = [result("billing", old), result("technical", new)] + ([result("account_ops", record)] if record
                                                                     else [])
    jev, draft = Jev(support={c["text"]: 5 for c in (old, new) + ((record,) if record else ())}), Drafts()
    outcome = merge(DecisionEngine(jev), results, {**EVIDENCE, "tool.record": "TKT-1"}, draft, dated=dated)
    if record is None:
        assert outcome.kind == "reply" and [c.text for c in draft.calls[0]] == [new["text"]]
    else:  # the record still conflicts with the newest article: Jev settles it as before
        assert outcome.reason == "EVIDENCE_AMBIGUITY" and set(outcome.detail["claims"]) == {"technical",
                                                                                            "account_ops"}


def test_same_date_or_no_dates_keep_the_conflict_check():
    outcome, _, _ = run([result("technical", A), result("billing", B)], Jev(support={A["text"]: 3,
                                                                                    B["text"]: 3}))
    assert outcome.reason == "EVIDENCE_AMBIGUITY"
    same = merge(DecisionEngine(Jev(support={A["text"]: 3, B["text"]: 3})),
                 [result("technical", A), result("billing", B)], EVIDENCE, Drafts(),
                 dated={"ops.uptime": "2026-09-15", "billing.credit_memos": "2026-09-15"})
    assert same.reason == "EVIDENCE_AMBIGUITY"


def test_three_way_conflicts_need_one_clear_winner():
    c = claim("root_cause", "A network change caused the outage", "kb.sla")
    jev = Jev(support={A["text"]: 5, B["text"]: 1, c["text"]: 3})
    outcome, _, _ = run([result("technical", A), result("billing", B), result("generalist", c)], jev)
    assert outcome.reason == "EVIDENCE_AMBIGUITY"


@pytest.mark.parametrize("jev", [Jev(down=True), Jev(support={A["text"]: 9, B["text"]: 1})])
def test_jev_down_or_malformed_goes_to_a_person(jev):
    outcome, _, draft = run([result("technical", A), result("billing", B)], jev)
    assert outcome.reason == "JEV_UNAVAILABLE" and draft.calls == []


def test_personal_data_never_goes_to_jev():
    leaky = claim("root_cause", "sarah@acme.com changed the config", "ops.uptime")
    with pytest.raises(SecurityViolation):
        run([result("technical", leaky), result("billing", B)], Jev(support={B["text"]: 1}))


# One reply, one voice ---------------------------------------------------------------------------------------

def test_one_reply_from_the_coordinator_built_from_the_accepted_claims():
    outcome, _, draft = run([result("technical", UPTIME), result("billing", CREDITS)])
    assert outcome.kind == "reply" and outcome.text == draft.text and len(draft.calls) == 1
    assert {c.topic for c in draft.calls[0]} == {"eu_uptime_q2", "credits_q2"}


@pytest.mark.parametrize("asks, kind", [
    (("hand_off", 0.9), "human"), (("both", 0.6), "human"), (("hand_off", 0.5), "human"),
    (("hand_off", 0.45), "reply"),       # Jev not more sure than not
    (("self_service", 0.95), "reply"),   # e.g. "ask another Tenant Owner": the customer's own people
])
def test_a_reply_that_hands_the_customer_to_someone_at_the_provider_is_held(asks, kind):
    """SWEEP-07 (register B25): a choice, not a yes / no, so self-service through the customer's own people
    or forms is not mistaken for a redirect."""
    outcome, _, _ = run([result("billing", CREDITS)], Jev(redirect=asks),
                        Drafts("Please contact the billing department about CM-7."))
    assert outcome.kind == kind and outcome.detail["redirect"] == asks[0]
    if kind == "human":
        assert outcome.reason == "REDIRECT" and "billing department" in outcome.detail["draft"]


def test_the_redirect_question_names_the_three_choices_without_numbers():
    assert REDIRECT["type"] == "choice" and set(REDIRECT["criteria"]) == {"self_service", "hand_off", "both"}


class RedirectByDraft(Jev):
    """What each draft asks the customer to do."""

    def __init__(self, scores):
        super().__init__()
        self.scores = scores

    def evaluate(self, state, questions):
        self.redirect = self.scores[state["draft"]]
        return super().evaluate(state, questions)


ELSEWHERE = "Please contact the billing department about CM-7."
SELF_SERVE = "CM-7 was applied to your invoice."


@pytest.mark.parametrize("second, kind", [(("self_service", 0.9), "reply"), (("hand_off", 0.8), "human")])
def test_a_redirecting_draft_is_redrafted_once_and_checked_again(second, kind):
    """SWEEP-05 / SWEEP-07 (register B25): one redraft when the first draft points elsewhere; the check and
    an excerpt of the held draft are kept for the internal trace."""
    redrafts = Drafts(SELF_SERVE)
    outcome = merge(DecisionEngine(RedirectByDraft({ELSEWHERE: ("hand_off", 0.95), SELF_SERVE: second})),
                    [result("billing", CREDITS)], EVIDENCE, Drafts(ELSEWHERE), redraft=redrafts)
    assert outcome.kind == kind and len(redrafts.calls) == 1
    assert (outcome.detail["redrafted"], outcome.detail["rejected_draft"]) == (True, ELSEWHERE)
    assert (outcome.detail["redirect"], outcome.detail["redirect_confidence"]) == second
    if kind == "reply":
        assert outcome.text == SELF_SERVE
    else:
        assert outcome.reason == "REDIRECT"


def test_a_draft_that_passes_is_not_redrafted_and_records_the_check():
    redrafts = Drafts(SELF_SERVE)
    outcome = merge(DecisionEngine(Jev()), [result("billing", CREDITS)], EVIDENCE, Drafts(SELF_SERVE),
                    redraft=redrafts)
    assert outcome.kind == "reply" and redrafts.calls == []
    assert outcome.detail == {"redirect": "self_service", "redirect_confidence": 0.95, "redrafted": False}


def test_jev_down_at_the_redirect_check_holds_the_reply():
    class DownLater(Jev):
        def evaluate(self, state, questions):
            if "redirect" in questions:
                raise JevUnavailable("down")
            return super().evaluate(state, questions)
    outcome, _, _ = run([result("billing", CREDITS)], DownLater())
    assert outcome.reason == "JEV_UNAVAILABLE"


DONE = ExecutedAction(tool_id="billing.tag_invoice", tool_call_id="c-1", verified=True)


@pytest.mark.parametrize("text, verified, kind", [
    ("I have processed your refund.", (), "human"),
    ("Your credit has been applied.", (), "human"),
    ("The account is now reset.", (), "human"),
    ("I have prepared a credit memo for your review.", (), "reply"),       # prospective wording
    ("I have processed the tag on your invoice.", (DONE,), "reply"),       # a verified write
])
def test_completed_actions_are_claimed_only_when_a_verified_write_backs_them(text, verified, kind):
    outcome, _, _ = run([result("billing", CREDITS)], draft=Drafts(text), verified=verified)
    assert outcome.kind == kind
    if kind == "human":
        assert outcome.reason == "UNVERIFIED_COMPLETION"


def test_a_claim_model_refuses_unknown_fields():
    with pytest.raises(ValueError):
        Claim(topic="t", text="x", evidence=("e",), voice="customer")
