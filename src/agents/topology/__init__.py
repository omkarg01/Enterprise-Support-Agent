"""The coordinator and its specialists (FR-25 / MA-ADP-01: MA-D1, MA-D2, MA-D10, MA-D11; FR-26 / MA-ADP-02:
MA-D3, MA-D6, MA-D8).

- registry(): the static specialist set (generalist, billing, technical, account_ops), fixed at start-up and
  read-only afterwards. Each has a description Jev reads (no digits, the Jev rule) and a tool allow-list of
  registered, read-only tools (writes come with the approval rules of MA-ADP-04); the coordinator has no
  tools.
- delegate(): one Jev request over the masked message. A choice picks the lead (below 0.50 confidence
  nothing is dispatched and the case goes to a person); a yes / no per other domain specialist adds it at
  P >= 0.80; a yes / no per added specialist says whether it needs the lead's findings first. Jev
  unavailable or invalid (DecisionEngine checks every answer's shape, option and confidence) -> a person.
  Step budgets are handed out before anything runs, so parallel branches cannot overrun them: the
  coordinator's delegation is one step, the lead gets up to two, then the others in order while the turn's
  six last; a specialist with no budget left is not dispatched, so a turn has at most three specialists.
- coordinator(): a LangGraph graph, started with start_turn() on the case's own thread. The plan is checked
  again wherever it is read (roles, budgets, the turn total), so a malformed or inflated plan in state is
  refused, not run. Independent specialists run in parallel (Send); dependent ones run after the lead, only
  when the lead completed, with its findings in their brief; otherwise they are recorded as skipped. Each
  specialist is its own compiled subgraph run inside the coordinator's node, so its state is checkpointed
  with the case's. Depth is one: a specialist can only call tools on its allow-list, never another agent, and
  returns validated findings to the coordinator, never text to the customer. Results are kept in role order.
- Tools run through run_tool(ToolRequest): the turn's tenant, case, session and principal come from the
  turn; executor_tool() runs it through tools.execution.Executor, which verifies the principal against the
  live session, mints the credential, validates the arguments and screens the output. The call id is
  deterministic per turn, specialist and step, so a retried node reuses the same idempotency key. A
  specialist sees a tool's screened text or the failure's code, never an exception message.
"""

import hashlib
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from types import MappingProxyType
from typing import Annotated, Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from core.orchestrator.decisions import NUMBERS, JevUnavailable, Masked, SecurityViolation
from core.orchestrator.statechart import thread
from safety.pii.tokens import find as find_pii
from tools.execution.calls import ToolCall
from tools.registry import Risk

STEPS_PER_SPECIALIST = 2
STEPS_PER_TURN = 6      # all agents, the coordinator included
COORDINATOR_STEPS = 1   # the delegation decision
LEAD_AT = 0.50
CO_ACTIVATE_AT = 0.80
MAX_FINDINGS = 4000     # characters
MAX_EVIDENCE = 20


class Role(str, Enum):
    GENERALIST = "generalist"
    BILLING = "billing"
    TECHNICAL = "technical"
    ACCOUNT_OPS = "account_ops"


@dataclass(frozen=True)
class Specialist:
    """act(brief, history) -> {"thought", "action"?, "action_input"?, "findings"?, "evidence"?}: one ReAct
    step; a step with findings ends the specialist's work."""

    role: Role
    description: str
    tools: frozenset[str]
    act: Callable[[dict, list], dict] = field(compare=False)


def registry(specialists, tool_registry):
    """The static registry {Role: Specialist}; refuses anything but one specialist per role, descriptions with
    digits, and tools that are not registered (tool_registry: a frozen tools.registry) or not read-only."""
    by_role = {}
    for spec in specialists:
        role = Role(spec.role)
        if role in by_role:
            raise ValueError(f"{role.value} is registered twice")
        if NUMBERS.search(spec.description):
            raise ValueError(f"{role.value}: the description is shown to Jev and must not contain numbers")
        unknown = sorted(set(spec.tools) - set(tool_registry.tools))
        if unknown:
            raise ValueError(f"{role.value}: unknown tools {unknown}")
        writes = sorted(t for t in spec.tools if tool_registry.tools[t].risk is not Risk.READ_ONLY)
        if writes:
            raise ValueError(f"{role.value}: specialists may only read for now, not {writes}")
        by_role[role] = replace(spec, role=role, tools=frozenset(spec.tools))
    if set(by_role) != set(Role):
        raise ValueError(f"missing specialists: {sorted(r.value for r in set(Role) - set(by_role))}")
    return MappingProxyType(by_role)


@dataclass(frozen=True)
class Assignment:
    role: Role
    steps: int
    after_lead: bool = False


@dataclass(frozen=True)
class Plan:
    lead: Role | None
    assignments: tuple[Assignment, ...] = ()
    lead_confidence: float = 0.0
    handoff: str | None = None   # why nothing was dispatched

    @property
    def steps(self):
        return COORDINATOR_STEPS + sum(a.steps for a in self.assignments)

    def as_state(self):
        """Plain JSON for the checkpoint."""
        return {"lead": self.lead and self.lead.value, "lead_confidence": self.lead_confidence,
                "handoff": self.handoff,
                "assignments": [{"role": a.role.value, "steps": a.steps, "after_lead": a.after_lead}
                                for a in self.assignments]}


def checked_plan(plan):
    """The plan read back from state, or ValueError: known, distinct roles, the lead first and not waiting,
    each budget one or two steps, the turn within six; a handoff dispatches nobody."""
    assignments = plan.get("assignments") if isinstance(plan, dict) else None
    if not isinstance(assignments, list) or not all(isinstance(a, dict) for a in assignments):
        raise ValueError("malformed plan")
    if plan.get("handoff"):
        if assignments:
            raise ValueError("a handoff plan dispatches nobody")
        return plan
    roles = [Role(a.get("role")) for a in assignments]
    steps = [a.get("steps") for a in assignments]
    if not roles or len(set(roles)) != len(roles) or roles[0] != Role(plan.get("lead")):
        raise ValueError("the plan must start with its lead and name each specialist once")
    if any(type(s) is not int or not 1 <= s <= STEPS_PER_SPECIALIST for s in steps):
        raise ValueError("a specialist's budget is one or two steps")
    if any(type(a.get("after_lead")) is not bool for a in assignments) or assignments[0]["after_lead"]:
        raise ValueError("malformed dependency")
    if COORDINATOR_STEPS + sum(steps) > STEPS_PER_TURN:
        raise ValueError("the plan exceeds the turn's six steps")
    return plan


def _yes(answer):
    return answer.confidence if answer.value is True else 1 - answer.confidence


def delegate(engine, specialists, message):
    """The delegation plan for one masked customer message."""
    lead_q = {"type": "choice",
              "instructions": "Which specialist should take the lead on this customer inquiry?",
              "criteria": {role.value: spec.description for role, spec in specialists.items()}}
    others = [role for role in specialists if role is not Role.GENERALIST]
    questions = {"lead": lead_q}
    for role in others:
        name = role.value.replace("_", " ")
        questions[f"needs_{role.value}"] = {"type": "noul", "instructions": (
            f"Does addressing this inquiry require factual input or action from the {name} specialist?")}
        questions[f"after_{role.value}"] = {
            "type": "noul", "instructions": f"Does the {name} part of this inquiry depend on what the lead "
                                            "specialist finds first?"}
    try:
        answers = engine.ask({"message": message}, questions)
    except JevUnavailable:  # down, or an answer of the wrong shape, option or confidence
        return Plan(None, handoff="delegation could not be decided")
    lead, confidence = Role(answers["lead"].value), answers["lead"].confidence
    if confidence < LEAD_AT:
        return Plan(None, lead_confidence=confidence, handoff="no specialist is a confident fit")
    chosen = [(lead, False)] + [
        (role, _yes(answers[f"after_{role.value}"]) >= CO_ACTIVATE_AT) for role in others
        if role is not lead and _yes(answers[f"needs_{role.value}"]) >= CO_ACTIVATE_AT]
    budget, assignments = STEPS_PER_TURN - COORDINATOR_STEPS, []
    for role, after_lead in chosen:
        steps = min(STEPS_PER_SPECIALIST, budget)
        if steps:
            assignments.append(Assignment(role, steps, after_lead))
            budget -= steps
    return Plan(lead, tuple(assignments), confidence)


# Tools ------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolRequest:
    """One specialist tool call with the turn's context; nothing here is a credential or a role."""

    tenant_id: str
    case_id: str
    session_id: str
    principal_id: str     # claimed; the executor checks it against the live session
    turn_id: str
    role: str
    step: int
    tool_id: str
    arguments: dict

    @property
    def tool_call_id(self):
        """The same on every attempt of this step, so a retried write reuses its idempotency key."""
        key = f"{self.turn_id}:{self.role}:{self.step}:{self.tool_id}"
        return hashlib.sha256(key.encode()).hexdigest()[:32]


def executor_tool(executor):
    """run_tool over tools.execution.Executor: returns the screened, spotlighted text a model may read."""
    def run(request):
        return executor.run(ToolCall(
            tool_call_id=request.tool_call_id, tool_id=request.tool_id, arguments=request.arguments,
            tenant_id=request.tenant_id, principal_id=request.principal_id, case_id=request.case_id,
            checkpoint_id=request.turn_id, session_id=request.session_id)).content
    return run


def failure_code(error):
    """What a specialist may see of a failed call: the executor's code, or the exception's class."""
    code = getattr(error, "code", None)
    return code if isinstance(code, str) and code.isupper() else type(error).__name__


# The graphs -------------------------------------------------------------------------------------------------

def merge_results(old, new):
    """Specialist results in role order, whatever order parallel branches finish in."""
    return sorted([*old, *new], key=lambda r: list(Role).index(Role(r["role"])))


class SpecialistState(TypedDict, total=False):
    brief: dict
    history: list
    result: dict


class TurnState(TypedDict, total=False):
    tenant_id: str
    case_id: str
    session_id: str
    principal_id: str
    turn_id: str
    message: str        # masked
    plan: dict          # Plan.as_state()
    results: Annotated[list, merge_results]
    outcome: str


TURN_FIELDS = ("tenant_id", "case_id", "session_id", "principal_id", "message")


def masked_text(message):
    """The message as masked text, or SecurityViolation when personal data is still in it."""
    text = message.text if isinstance(message, Masked) else message
    if not isinstance(text, str) or find_pii(text):
        raise SecurityViolation("the coordinator takes masked text only")
    return text


def _result(role, status, findings, history, evidence=(), **extra):
    return {"role": role, "status": status, "findings": findings, "evidence": list(evidence),
            "steps": len(history), **extra}


def valid_findings(move):
    findings, evidence = move.get("findings"), move.get("evidence", [])
    return (isinstance(findings, str) and len(findings) <= MAX_FINDINGS and isinstance(evidence, list)
            and len(evidence) <= MAX_EVIDENCE and all(isinstance(e, str) for e in evidence))


def specialist_graph(spec, run_tool):
    """One specialist: up to brief["steps"] ReAct steps over its own tools, then a typed result. A turn may
    bring its own run_tool in config["configurable"]["run_tool"] (DECISION-ea5b8887 C); else this one."""
    def step(state, config):
        brief, history = state["brief"], list(state.get("history", []))
        run = (config.get("configurable") or {}).get("run_tool") or run_tool
        if type(brief.get("steps")) is not int or not 1 <= brief["steps"] <= STEPS_PER_SPECIALIST:
            return {"result": _result(spec.role.value, "blocked", "invalid step budget", history)}
        move = spec.act(brief, history)
        history.append({key: move.get(key) for key in ("thought", "action")})
        if move.get("findings") is not None:
            if not valid_findings(move):
                return {"history": history,
                        "result": _result(spec.role.value, "blocked", "invalid findings", history)}
            return {"history": history, "result": _result(spec.role.value, "completed", move["findings"],
                                                          history, move.get("evidence", []))}
        action, arguments = move.get("action"), move.get("action_input", {})
        if action not in spec.tools or not isinstance(arguments, dict):
            return {"history": history,
                    "result": _result(spec.role.value, "blocked", f"refused action {action!r}", history)}
        request = ToolRequest(brief["tenant_id"], brief["case_id"], brief["session_id"],
                              brief["principal_id"], brief["turn_id"], spec.role.value, len(history), action,
                              arguments)
        try:
            output = run(request)
            observation = {"ok": output} if isinstance(output, str) else {"error": "UNSCREENED_OUTPUT"}
        except Exception as error:  # noqa: BLE001 - the next step may retry once (Reflexion), within budget
            observation = {"error": failure_code(error)}
        history[-1]["observation"] = observation
        if len(history) >= brief["steps"]:
            return {"history": history, "result": _result(spec.role.value, "blocked", "step budget used up",
                                                          history, last_observation=observation)}
        return {"history": history}

    graph = StateGraph(SpecialistState)
    graph.add_node("step", step)
    graph.add_edge(START, "step")
    graph.add_conditional_edges("step", lambda state: END if state.get("result") else "step", ["step", END])
    return graph.compile()


def coordinator(engine, specialists, run_tool, checkpointer=None):
    """The coordinator graph; start a turn with start_turn(). run_tool is the default; a turn passes its own
    as config["configurable"]["run_tool"], which reaches every specialist."""
    subgraphs = {role: specialist_graph(spec, run_tool) for role, spec in specialists.items()}

    def plan(state):
        if any(not isinstance(state.get(name), str) or not state[name] for name in TURN_FIELDS):
            raise ValueError("a turn needs its tenant, case, session, principal and message")
        message = masked_text(state["message"])
        return {"turn_id": state.get("turn_id") or str(uuid.uuid4()),
                "plan": delegate(engine, specialists, Masked(message)).as_state()}

    def run(task, config):
        brief = task["brief"]
        done = subgraphs[Role(brief["role"])].invoke({"brief": brief, "history": []}, config)
        return {"results": [done["result"]]}

    def briefs(state, after_lead):
        current = checked_plan(state["plan"])
        lead = next((r for r in state.get("results", []) if r["role"] == current["lead"]), None)
        context = {name: state[name] for name in (*TURN_FIELDS[:4], "turn_id")}
        sends, skipped = [], []
        for a in current["assignments"]:
            if a["after_lead"] != after_lead:
                continue
            if after_lead and (lead is None or lead["status"] != "completed"):
                skipped.append(a["role"])
                continue
            sends.append(Send("then" if after_lead else "first", {"brief": {
                **context, "role": a["role"], "steps": a["steps"], "objective": state["message"],
                "lead": current["lead"], "upstream": lead if after_lead else None}}))
        return sends, skipped

    def between(state):
        _, skipped = briefs(state, True)
        return {"results": [_result(role, "skipped", "the lead did not complete", []) for role in skipped]}

    def join(state):
        current = checked_plan(state["plan"])
        ledger(state.get("results", []), current)
        return {"outcome": "handoff" if current["handoff"] else "delegated"}

    graph = StateGraph(TurnState)
    graph.add_node("plan", plan)
    graph.add_node("first", run)
    graph.add_node("between", between)
    graph.add_node("then", run)
    graph.add_node("join", join)
    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", lambda s: briefs(s, False)[0] or "join", ["first", "join"])
    graph.add_edge("first", "between")
    graph.add_conditional_edges("between", lambda s: briefs(s, True)[0] or "join", ["then", "join"])
    graph.add_edge("then", "join")
    graph.add_edge("join", END)
    return graph.compile(checkpointer=checkpointer)


def start_turn(graph, *, tenant_id, case_id, session_id, principal_id, message):
    """Run one coordinator turn on the case's own thread (tenant:case). message: masked text (Masked); text
    that still holds personal data is refused before anything is checkpointed."""
    text = masked_text(message)
    return graph.invoke({"tenant_id": tenant_id, "case_id": case_id, "session_id": session_id,
                         "principal_id": principal_id, "message": text}, thread(tenant_id, case_id))


def ledger(results, plan):
    """Steps per specialist and in the turn, checked against the plan: each specialist once, within its own
    budget, the turn within six. Returns (per specialist, total); ValueError otherwise."""
    budgets = {a["role"]: a["steps"] for a in checked_plan(plan)["assignments"]}
    per = {}
    for r in results:
        if r["role"] in per or r["role"] not in budgets or not 0 <= r["steps"] <= budgets[r["role"]]:
            raise ValueError(f"{r['role']}: ran outside its assignment")
        per[r["role"]] = r["steps"]
    total = COORDINATOR_STEPS + sum(per.values())
    if total > STEPS_PER_TURN:
        raise ValueError("the turn ran more than six steps")
    return per, total
