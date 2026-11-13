"""M3-01: the coordinator, the static specialist registry, Jev delegation and the depth / span / step limits
(FR-25, FR-26 / MA-ADP-01, MA-ADP-02), with the security review's fixes: trusted turn context for tools,
dependency and budget checks, sanitised failures, masked input only."""

import subprocess
import threading
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel

from agents.topology import (
    STEPS_PER_TURN,
    Assignment,
    Role,
    Specialist,
    ToolRequest,
    checked_plan,
    coordinator,
    delegate,
    executor_tool,
    ledger,
    registry,
    specialist_graph,
    start_turn,
)
from core.orchestrator.decisions import Answer, DecisionEngine, JevUnavailable, Masked, SecurityViolation
from core.orchestrator.statechart import postgres_checkpointer, thread
from data.migrate import psql
from tests.tools.execution.test_execution import Fake, build_registry, executor
from tools.registry import Registry
from tools.schemas import arg

ROOT = Path(__file__).resolve().parents[3]
AUDIT = ("We need an audit comparing our EU database uptime for the quarter against our SLA and any credits "
         "applied.")
TOOLS = {"generalist": {"kb.search"}, "billing": {"billing.credit_memos"}, "technical": {"ops.uptime"},
         "account_ops": {"directory.users"}}
DESCRIPTIONS = {"generalist": "Product questions and general guidance",
                "billing": "Invoices, credits and refunds",
                "technical": "Outages, uptime, latency and infrastructure",
                "account_ops": "SSO, users and roles"}
TURN = {"tenant_id": "t_us", "case_id": "case-7", "session_id": "s-sarah", "principal_id": "sarah"}
SECRET = "password=hunter2 sk_live_4eC39HqLyjWDarjtT1zdp7dc"


class QueryArgs(BaseModel):
    q: str = arg(sensitive=True)


def tool_registry():
    reg = Registry()
    for tool_id in [*set().union(*TOOLS.values()), "billing.issue_credit"]:
        risk = "low_risk_write" if tool_id == "billing.issue_credit" else "read_only"
        reg.tool(tool_id, args=QueryArgs, description="A test tool", risk=risk, stages={"DELIBERATING"},
                 author="ana", reviewed_by="ben")(lambda a: {})
    return reg.freeze()


TOOL_REGISTRY = tool_registry()


class Jev:
    """lead: (role, confidence); needs / after: {role: P(yes)}; answers: raw overrides (malformed output)."""

    def __init__(self, lead=("technical", 0.9), needs=None, after=None, down=False, answers=None):
        self.lead, self.needs, self.after, self.down, self.raw = lead, needs or {}, after or {}, down, answers
        self.seen = []

    def evaluate(self, state, questions):
        self.seen.append((state, questions))
        if self.down:
            raise JevUnavailable("down")
        answers = {"lead": Answer(self.lead[0], self.lead[1])}
        for qid in questions:
            kind, _, role = qid.partition("_")
            if kind in ("needs", "after"):
                p = getattr(self, kind).get(role, 0.0)
                answers[qid] = Answer(p >= 0.5, max(p, 1 - p))
        return {**answers, **(self.raw or {})}


def finishing(role, tool):
    """Reads its tool once, then reports what it saw (and what the lead found, when it waited for it)."""
    def act(brief, history):
        if not history:
            return {"thought": "look it up", "action": tool, "action_input": {"q": brief["objective"]}}
        upstream = f" after {brief['upstream']['findings']}" if brief.get("upstream") else ""
        return {"thought": "report", "findings": f"{role}: {history[-1]['observation']}{upstream}",
                "evidence": [tool]}
    return act


def endless(tool):
    def act(brief, history):
        return {"thought": "one more look", "action": tool, "action_input": {"q": "x"}}
    return act


def specialists(**acts):
    return registry([Specialist(role, DESCRIPTIONS[role], frozenset(TOOLS[role]),
                                acts.get(role) or finishing(role, next(iter(TOOLS[role]))))
                     for role in TOOLS], TOOL_REGISTRY)


class Tools:
    def __init__(self, fail_once=(), raise_with=None, output="ok"):
        self.calls, self.requests, self.fail_once, self.raise_with = [], [], set(fail_once), raise_with
        self.output, self.lock = output, threading.Lock()

    def __call__(self, request):
        with self.lock:
            self.calls.append((request.role, request.tool_id))
            self.requests.append(request)
        if self.raise_with:
            raise self.raise_with
        if request.tool_id in self.fail_once:
            self.fail_once.discard(request.tool_id)
            raise TimeoutError("the API timed out")
        return f"{request.tool_id} {self.output}"


def turn(jev, tools=None, checkpointer=None, **acts):
    tools = tools or Tools()
    graph = coordinator(DecisionEngine(jev), specialists(**acts), tools, checkpointer)
    return start_turn(graph, **TURN, message=Masked(AUDIT)), tools


def roles(state):
    return [r["role"] for r in state["results"]]


def looks_then_reports(seen, findings="done", tool="ops.uptime"):
    def act(brief, history):
        if history:
            seen.append(history[-1]["observation"])
            return {"thought": "report", "findings": findings}
        return {"thought": "look", "action": tool, "action_input": {"q": "x"}}
    return act


# The registry -----------------------------------------------------------------------------------------------

def test_the_registry_is_the_four_specialists_fixed_at_start_up():
    reg = specialists()
    assert set(reg) == set(Role)
    with pytest.raises(TypeError):
        reg[Role.BILLING] = reg[Role.TECHNICAL]
    spec = reg[Role.BILLING]
    with pytest.raises(ValueError, match="registered twice"):
        registry([spec, spec], TOOL_REGISTRY)
    with pytest.raises(ValueError, match="missing specialists"):
        registry([spec], TOOL_REGISTRY)
    with pytest.raises(ValueError, match="must not contain numbers"):
        registry([Specialist("billing", "Refunds over 1000 USD", frozenset(), spec.act)], TOOL_REGISTRY)
    with pytest.raises(ValueError, match="unknown tools"):
        registry([Specialist("billing", "Refunds", frozenset({"billing.wire_money"}), spec.act)],
                 TOOL_REGISTRY)
    with pytest.raises(ValueError):
        registry([Specialist("coordinator", "Routes", frozenset(), spec.act)], TOOL_REGISTRY)


def test_a_specialist_cannot_be_given_a_write_so_no_action_skips_its_approval():
    with pytest.raises(ValueError, match="may only read"):
        registry([Specialist("billing", "Refunds", frozenset({"billing.issue_credit"}), endless("x"))],
                 TOOL_REGISTRY)


# Delegation -------------------------------------------------------------------------------------------------

def test_jev_picks_the_lead_from_the_registry_on_masked_text():
    jev = Jev(lead=("billing", 0.93))
    plan = delegate(DecisionEngine(jev), specialists(), Masked("Why was I charged twice?"))
    assert plan.lead is Role.BILLING and plan.assignments == (Assignment(Role.BILLING, 2),)
    (_, questions), = jev.seen
    assert set(questions["lead"]["criteria"]) == {"generalist", "billing", "technical", "account_ops"}
    assert "needs_generalist" not in questions  # the generalist leads or stays out


def test_other_specialists_join_at_high_confidence_and_say_whether_they_wait_for_the_lead():
    plan = delegate(DecisionEngine(Jev(needs={"billing": 0.85, "account_ops": 0.79}, after={"billing": 0.9})),
                    specialists(), Masked(AUDIT))
    assert plan.lead is Role.TECHNICAL
    assert plan.assignments == (Assignment(Role.TECHNICAL, 2), Assignment(Role.BILLING, 2, after_lead=True))


@pytest.mark.parametrize("jev, reason", [
    (Jev(lead=("billing", 0.49)), "no specialist is a confident fit"),
    (Jev(down=True), "delegation could not be decided"),
    (Jev(lead=("wire_money", 0.99)), "delegation could not be decided"),           # not a registry option
    (Jev(lead=("billing", 1.5)), "delegation could not be decided"),               # confidence out of range
    (Jev(lead=("billing", float("nan"))), "delegation could not be decided"),
    (Jev(answers={"needs_billing": Answer("yes", 0.9)}), "delegation could not be decided"),  # not a bool
    (Jev(answers={"lead": None}), "delegation could not be decided"),              # an answer missing
])
def test_an_unsure_missing_or_malformed_decision_dispatches_nobody_and_goes_to_a_person(jev, reason):
    plan = delegate(DecisionEngine(jev), specialists(), Masked(AUDIT))
    assert plan.assignments == () and plan.handoff == reason
    state, tools = turn(jev)
    assert state["outcome"] == "handoff" and state.get("results", []) == [] and tools.calls == []


def test_step_budgets_are_handed_out_before_anything_runs_and_never_exceed_the_turn():
    everyone = {"billing": 0.9, "account_ops": 0.9, "technical": 0.9}
    engine = DecisionEngine(Jev(lead=("generalist", 0.8), needs=everyone))
    plan = delegate(engine, specialists(), Masked(AUDIT))
    assert [(a.role.value, a.steps) for a in plan.assignments] == [("generalist", 2), ("billing", 2),
                                                                  ("technical", 1)]
    assert plan.steps == STEPS_PER_TURN  # account_ops would exceed the six and is not dispatched
    assert len(plan.assignments) == 3 < 5  # the step budget keeps the span far inside five


# Plans read back from state are checked ---------------------------------------------------------------------

GOOD = {"lead": "technical", "lead_confidence": 0.9, "handoff": None,
        "assignments": [{"role": "technical", "steps": 2, "after_lead": False},
                        {"role": "billing", "steps": 2, "after_lead": True}]}


@pytest.mark.parametrize("change", [
    {"assignments": [{"role": "technical", "steps": 3, "after_lead": False}]},            # over two steps
    {"assignments": [{"role": "technical", "steps": 0, "after_lead": False}]},
    {"assignments": [{"role": "technical", "steps": "2", "after_lead": False}]},
    {"assignments": [{"role": "technical", "steps": True, "after_lead": False}]},
    {"assignments": [{"role": "technical", "steps": 2, "after_lead": False}] * 2},         # a role twice
    {"assignments": [{"role": r, "steps": 2, "after_lead": False}
                     for r in ("technical", "billing", "account_ops")]},                  # seven steps
    {"assignments": [{"role": "billing", "steps": 2, "after_lead": False}]},              # not the lead
    {"assignments": [{"role": "technical", "steps": 2, "after_lead": True}]},             # the lead waits
    {"assignments": [{"role": "technical", "steps": 2, "after_lead": "no"}]},
    {"assignments": [{"role": "coordinator", "steps": 2, "after_lead": False}]},
    {"handoff": "x"},                                                                     # handoff + work
    {"assignments": "all"},
])
def test_a_malformed_or_inflated_plan_is_refused(change):
    assert checked_plan(GOOD) is GOOD
    with pytest.raises(ValueError):
        checked_plan({**GOOD, **change})


def test_a_plan_changed_in_the_checkpoint_before_resuming_is_refused_before_any_tool_runs():
    saver, tools = InMemorySaver(), Tools()
    graph = coordinator(DecisionEngine(Jev()), specialists(), tools, saver)
    config = thread("t_us", "case-7")
    graph.invoke({**TURN, "message": AUDIT}, config, interrupt_before=["first"])
    inflated = {**GOOD, "assignments": [{"role": "technical", "steps": 9, "after_lead": False}]}
    with pytest.raises(ValueError, match="one or two steps"):  # refused when the next step is planned
        graph.update_state(config, {"plan": inflated}, as_node="plan")
        graph.invoke(None, config)
    assert tools.calls == []


@pytest.mark.parametrize("steps", [0, 3, -1, "2", None])
def test_a_specialist_given_a_bad_budget_directly_does_nothing(steps):
    tools = Tools()
    graph = specialist_graph(specialists()[Role.TECHNICAL], tools)
    brief = {**TURN, "turn_id": "turn-1", "role": "technical", "steps": steps, "objective": "x"}
    result = graph.invoke({"brief": brief, "history": []})["result"]
    assert result["status"] == "blocked" and result["findings"] == "invalid step budget" and tools.calls == []


def test_the_ledger_checks_what_ran_against_the_plan():
    ran = [{"role": "technical", "steps": 2}, {"role": "billing", "steps": 2}]
    assert ledger(ran, GOOD) == ({"technical": 2, "billing": 2}, 5)
    for results in ([{"role": "technical", "steps": 3}], [{"role": "account_ops", "steps": 1}],
                    [{"role": "technical", "steps": 1}, {"role": "technical", "steps": 1}]):
        with pytest.raises(ValueError, match="outside its assignment"):
            ledger(results, GOOD)


# The coordinator graph --------------------------------------------------------------------------------------

def test_independent_specialists_run_in_parallel_within_their_steps():
    state, tools = turn(Jev(needs={"billing": 0.9, "account_ops": 0.9}))
    assert state["outcome"] == "delegated"
    assert sorted(tools.calls) == [("account_ops", "directory.users"), ("billing", "billing.credit_memos"),
                                   ("technical", "ops.uptime")]
    results = {r["role"]: r for r in state["results"]}
    assert results["technical"]["findings"] == "technical: {'ok': 'ops.uptime ok'}"
    assert roles(state) == ["billing", "technical", "account_ops"]  # role order, whoever finished first
    assert [r["steps"] for r in state["results"]] == [2, 2, 1]       # account_ops got the one step left
    assert ledger(state["results"], state["plan"])[1] == STEPS_PER_TURN


def test_several_dependents_run_after_the_lead_and_each_gets_its_completed_result():
    seen = []

    def watching(role, tool):
        def act(brief, history):
            seen.append((role, brief["upstream"] and brief["upstream"]["status"]))
            return finishing(role, tool)(brief, history)
        return act
    state, tools = turn(Jev(lead=("generalist", 0.9), needs={"billing": 0.9, "technical": 0.9},
                            after={"billing": 0.9, "technical": 0.9}),
                        billing=watching("billing", "billing.credit_memos"),
                        technical=watching("technical", "ops.uptime"))
    assert tools.calls[0] == ("generalist", "kb.search")              # the lead ran first
    assert set(seen) == {("billing", "completed"), ("technical", "completed")}
    results = {r["role"]: r for r in state["results"]}
    assert "after generalist:" in results["billing"]["findings"]
    assert results["technical"]["steps"] == 1 and results["technical"]["status"] == "blocked"  # one step left


@pytest.mark.parametrize("lead_act", [
    endless("ops.uptime"),                                                         # blocked at its budget
    lambda brief, history: {"thought": "x", "action": "billing.credit_memos"},     # blocked: off its list
    lambda brief, history: {"thought": "x", "findings": 42},                       # invalid findings
])
def test_dependents_are_skipped_not_run_when_the_lead_did_not_complete(lead_act):
    state, tools = turn(Jev(needs={"billing": 0.9}, after={"billing": 0.9}), technical=lead_act)
    results = {r["role"]: r for r in state["results"]}
    assert results["technical"]["status"] == "blocked"
    assert results["billing"] == {"role": "billing", "status": "skipped",
                                  "findings": "the lead did not complete", "evidence": [], "steps": 0}
    assert all(role != "billing" for role, _ in tools.calls)


def test_a_specialist_that_knows_the_answer_reports_in_one_step():
    def direct(brief, history):
        return {"thought": "known", "findings": "use the reset link", "evidence": ["kb"]}
    state, tools = turn(Jev(lead=("generalist", 0.9)), generalist=direct)
    assert state["results"] == [{"role": "generalist", "status": "completed",
                                 "findings": "use the reset link", "evidence": ["kb"], "steps": 1}]
    assert tools.calls == [] and ledger(state["results"], state["plan"])[1] == 2


def test_a_specialist_that_never_finishes_is_blocked_at_its_budget_and_the_turn_stays_within_six():
    stuck = {role: endless(next(iter(TOOLS[role]))) for role in TOOLS}
    state, tools = turn(Jev(lead=("generalist", 0.8), needs={"billing": 0.9, "technical": 0.9,
                                                              "account_ops": 0.9}), **stuck)
    per, total = ledger(state["results"], state["plan"])
    assert per == {"generalist": 2, "billing": 2, "technical": 1} and total == STEPS_PER_TURN
    assert all(r["status"] == "blocked" and r["findings"] == "step budget used up" for r in state["results"])
    assert len(tools.calls) == 5


def test_a_failed_tool_call_gets_one_retry_within_the_budget():
    def retry(brief, history):
        if history and "error" in history[-1]["observation"] and len(history) == 1:
            return {"thought": "retry", "action": "ops.uptime", "action_input": {"q": "x"}}
        return finishing("technical", "ops.uptime")(brief, history)
    state, tools = turn(Jev(), Tools(fail_once={"ops.uptime"}), technical=retry)
    (result,) = state["results"]
    assert result["status"] == "blocked" and result["last_observation"] == {"ok": "ops.uptime ok"}
    assert tools.calls == [("technical", "ops.uptime"), ("technical", "ops.uptime")]


@pytest.mark.parametrize("action, arguments", [
    ("billing.credit_memos", {}), ("billing", {}), ("delegate", {}), ("send_message", {}), (None, {}),
    ("ops.uptime", "q=all"), ("ops.uptime", ["q"])])
def test_depth_one_a_specialist_only_reaches_its_own_tools_with_object_arguments(action, arguments):
    def overreach(brief, history):
        return {"thought": "hand it to someone else", "action": action, "action_input": arguments}
    state, tools = turn(Jev(), technical=overreach)
    (result,) = state["results"]
    assert result["status"] == "blocked" and result["findings"] == f"refused action {action!r}"
    assert tools.calls == []


def test_a_tool_failure_reaches_the_specialist_as_a_code_never_its_message():
    saver, seen = InMemorySaver(), []
    state, _ = turn(Jev(), Tools(raise_with=RuntimeError(SECRET)), saver, technical=looks_then_reports(seen))
    assert seen == [{"error": "RuntimeError"}]
    stored = str(list(saver.list(None)))
    assert "hunter2" not in stored and "sk_live" not in stored and "hunter2" not in str(state)


def test_only_screened_text_from_a_tool_reaches_a_specialist():
    class Raw(Tools):
        def __call__(self, request):
            super().__call__(request)
            return {"rows": ["raw"]}
    seen = []
    turn(Jev(), Raw(), technical=looks_then_reports(seen))
    assert seen == [{"error": "UNSCREENED_OUTPUT"}]


def test_tools_get_the_turns_context_and_a_call_id_that_is_stable_per_step():
    state, tools = turn(Jev(needs={"billing": 0.9}))
    request = next(r for r in tools.requests if r.role == "technical")
    assert (request.tenant_id, request.case_id, request.session_id, request.principal_id) == (
        "t_us", "case-7", "s-sarah", "sarah")
    assert request.turn_id == state["turn_id"] and request.step == 1
    assert ToolRequest(**vars(request)).tool_call_id == request.tool_call_id   # a retried step reuses its key
    assert ToolRequest(**{**vars(request), "step": 2}).tool_call_id != request.tool_call_id
    assert len({r.tool_call_id for r in tools.requests}) == len(tools.requests)


def test_a_turn_brings_its_own_run_tool_in_the_config_and_every_specialist_uses_it():
    """AGENT-02 (DECISION-ea5b8887 C): the graph is built once; each turn's tools reach it per call."""
    built_with, this_turn = Tools(), Tools(output="for this turn")
    graph = coordinator(DecisionEngine(Jev(needs={"billing": 0.9})), specialists(), built_with)
    state = graph.invoke({**TURN, "message": AUDIT}, {"configurable": {"run_tool": this_turn}})
    assert built_with.calls == [] and sorted(this_turn.calls) == [("billing", "billing.credit_memos"),
                                                                  ("technical", "ops.uptime")]
    assert all(r["status"] == "completed" for r in state["results"])


def test_specialists_return_findings_to_the_coordinator_not_text_to_the_customer():
    state, _ = turn(Jev(needs={"billing": 0.9}))
    assert set(state) <= {*TURN, "turn_id", "message", "plan", "results", "outcome"}
    assert all(set(r) <= {"role", "status", "findings", "evidence", "steps", "last_observation"}
               for r in state["results"])


@pytest.mark.parametrize("findings, evidence", [("x" * 4001, []), ("ok", "kb"), ("ok", [1]),
                                                ("ok", ["e"] * 21)])
def test_findings_are_checked_before_the_coordinator_uses_them(findings, evidence):
    def bad(brief, history):
        return {"thought": "report", "findings": findings, "evidence": evidence}
    state, _ = turn(Jev(), technical=bad)
    (result,) = state["results"]
    assert result["status"] == "blocked" and result["findings"] == "invalid findings"


# Masked input only ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("message", ["Refund sarah@acme.com please", Masked("Call me on +1 415 555 0100"),
                                     Masked("card 4111 1111 1111 1111")])
def test_unmasked_text_is_refused_before_jev_or_the_checkpoint_sees_it(message):
    saver, jev = InMemorySaver(), Jev()
    graph = coordinator(DecisionEngine(jev), specialists(), Tools(), saver)
    with pytest.raises(SecurityViolation):
        start_turn(graph, **TURN, message=message)
    assert jev.seen == [] and list(saver.list(None)) == []


def test_masked_tokens_are_accepted():
    graph = coordinator(DecisionEngine(Jev(lead=("billing", 0.9))), specialists(), Tools())
    state = start_turn(graph, **TURN, message=Masked("Refund <EMAIL_1a2b3c4d> please"))
    assert state["outcome"] == "delegated"


def test_a_turn_without_its_context_is_refused():
    graph = coordinator(DecisionEngine(Jev()), specialists(), Tools())
    for missing in TURN:
        with pytest.raises(ValueError, match="needs its tenant"):
            graph.invoke({**{k: v for k, v in TURN.items() if k != missing}, "message": AUDIT})


# Through the real executor: identity, tenant, schema and screening are enforced there -----------------------

def executor_turn(session_id="s-sarah", principal_id="sarah", tenant_id="t_us", arguments=None):
    fake, seen = Fake(), []
    run, _ = executor(fake)

    def billing(brief, history):
        if history:
            seen.append(history[-1]["observation"])
            return {"thought": "report", "findings": "read"}
        return {"thought": "read the invoice", "action": "billing.get_invoice",
                "action_input": arguments or {"invoice_id": "INV-9821"}}
    reg = registry([Specialist(role, DESCRIPTIONS[role], frozenset({"billing.get_invoice"}), billing)
                    for role in TOOLS], build_registry())
    graph = coordinator(DecisionEngine(Jev(lead=("billing", 0.9))), reg, executor_tool(run))
    start_turn(graph, tenant_id=tenant_id, case_id="case-1", session_id=session_id, principal_id=principal_id,
               message=Masked("Read my invoice"))
    return seen, fake


def test_through_the_executor_a_specialist_reads_screened_output_on_the_customers_session():
    seen, fake = executor_turn()
    assert len(fake.requests) == 1 and "INV-9821" in seen[0]["ok"] and seen[0]["ok"].startswith("<<<")


@pytest.mark.parametrize("options, code", [
    ({"tenant_id": "t_eu"}, "AUTHENTICATION_EXPIRED"),            # another tenant's turn on this session
    ({"principal_id": "bob"}, "FORBIDDEN"),                      # a principal the session does not have
    ({"session_id": "s-ended"}, "AUTHENTICATION_EXPIRED"),
    ({"arguments": {"invoice": "INV-9821"}}, "ValidationError"),  # the tool's argument schema
])
def test_through_the_executor_identity_tenant_and_arguments_are_enforced_before_any_request(options, code):
    seen, fake = executor_turn(**options)
    assert seen == [{"error": code}] and fake.requests == []


# Checkpoints ------------------------------------------------------------------------------------------------

def test_the_turn_and_its_specialists_are_checkpointed_on_the_cases_thread():
    saver = InMemorySaver()
    turn(Jev(needs={"billing": 0.9}), checkpointer=saver)
    saved = saver.get_tuple(thread("t_us", "case-7")).checkpoint["channel_values"]
    assert saved["plan"]["lead"] == "technical" and len(saved["results"]) == 2
    assert saver.get_tuple(thread("t_eu", "case-7")) is None


def test_an_interrupted_turn_resumes_without_rerunning_finished_specialists():
    saver, tools = InMemorySaver(), Tools()
    graph = coordinator(DecisionEngine(Jev(needs={"billing": 0.9}, after={"billing": 0.9})), specialists(),
                        tools, saver)
    config = thread("t_us", "case-7")
    graph.invoke({**TURN, "message": AUDIT}, config, interrupt_before=["between"])
    assert tools.calls == [("technical", "ops.uptime")]
    state = graph.invoke(None, config)
    assert tools.calls == [("technical", "ops.uptime"), ("billing", "billing.credit_memos")]
    assert roles(state) == ["billing", "technical"] and state["turn_id"] == tools.requests[0].turn_id


def test_the_plan_and_results_survive_the_postgres_checkpointer():
    subprocess.run(["docker", "compose", "-f", "deploy/compose.yaml", "--env-file", ".env.example",
                    "up", "-d", "--wait", "postgres"], cwd=ROOT, check=True, capture_output=True)
    database = "test_agents_topology"
    psql("postgres", f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
    psql("postgres", f'CREATE DATABASE "{database}"')
    with postgres_checkpointer(f"postgresql://agent:change-me@127.0.0.1:5432/{database}") as saver:
        turn(Jev(needs={"billing": 0.9}), checkpointer=saver)
        saved = saver.get_tuple(thread("t_us", "case-7")).checkpoint["channel_values"]
    assert saved["outcome"] == "delegated" and roles(saved) == ["billing", "technical"]
