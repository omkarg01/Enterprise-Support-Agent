"""System 1: the deterministic statechart for one case (FR-1 / ADP-01; FR-18 / MS-ADP-04).

Every stage is a node. A node may only be entered through a transition listed in TRANSITIONS (the ADP-01 guard
table); any non-terminal stage may also trip to ESCALATED_HITL (ADP-04). A run is a plain loop (no LangGraph,
DECISION-bc6301b4): run the node, merge its update (schemas.thread_state.merge), save the state to the case
state store (core.orchestrator.case_state), follow next_stage. The saved state's `pending` names the node the
run goes to next, so a crashed run resumes there with chart.invoke(None, thread(...)). Waiting stages end the
run; the next event (a customer message or an approval outcome) resumes the case.

The decisions inside the nodes are hooks, so the triage model (Jev), slot filling, SOP execution and the ReAct
step plug in later without changing the graph:

    triage(state) -> Triage-shaped dict           collect(state) -> {"parameters", "missing", "sop_name"?}
    execute_sop(state) -> {"response"} | {"error"}  react_step(state) -> Step-shaped dict
    revalidate(state) -> bool  (re-check the world before acting after a wait of more than an hour)
"""

import time

from core.orchestrator.case_state import MemoryStore
from core.orchestrator.planner import after_step
from schemas.thread_state import APPEND, Stage, Step, Triage, UnsupportedSchema, episode, merge, upgrade

S = Stage
TRANSITIONS = {
    None: {S.INGESTED},
    S.RESOLVED: {S.INGESTED},
    S.INGESTED: {S.TRIAGED},
    S.TRIAGED: {S.COLLECTING_PARAMS, S.DELIBERATING, S.AWAITING_CUSTOMER},
    S.COLLECTING_PARAMS: {S.EXECUTING_SOP, S.AWAITING_CUSTOMER},
    S.EXECUTING_SOP: {S.AWAITING_APPROVAL, S.RESOLVED},
    S.DELIBERATING: {S.DELIBERATING, S.EXECUTING_SOP, S.RESOLVED},
    S.AWAITING_APPROVAL: {S.EXECUTING_SOP, S.RESOLVED},
    S.AWAITING_CUSTOMER: {S.TRIAGED},
}
TERMINAL = {S.ESCALATED_HITL}
STALE_AFTER = 3600  # seconds of waiting after which the world is re-checked before acting (MS-D18)
DEFAULT_APPROVAL_THRESHOLD = 1000.0
MAX_NODES = 25      # nodes in one run; the bound LangGraph's recursion limit used to give


class IllegalTransition(RuntimeError):
    pass


def allowed(source, target):
    source = S(source) if source else None
    if target is S.ESCALATED_HITL:
        return source not in TERMINAL
    return target in TRANSITIONS.get(source, set())


def thread(tenant_id, case_id):
    """A case's config: one thread per (tenant, case). thread_id ("tenant:case") is the name the case's
    LangGraph checkpoints had (scripts/copy_langgraph_state.py); the statechart reads tenant_id and
    case_id."""
    return {"configurable": {"thread_id": f"{tenant_id}:{case_id}", "tenant_id": tenant_id,
                             "case_id": case_id}}


def _case(config):
    return config["configurable"]["tenant_id"], config["configurable"]["case_id"]


class Statechart:
    def __init__(self, nodes, store):
        self.nodes, self.store = nodes, store

    def get_state(self, config):
        """The case's saved state ({} for a new case)."""
        return self.store.load(*_case(config))[0]

    def invoke(self, event_input, config):
        """Apply `event_input` and run from the entry node until the case waits or ends; with None, carry on
        a crashed run from its pending node. Saves after every node; returns the final state."""
        key = _case(config)
        state, version = self.store.load(*key)
        state = {**{name: [] for name in APPEND}, **state}  # append fields start empty, as in LangGraph
        if event_input is not None:
            state = {**merge(state, event_input), "pending": "resume"}
            version = self.store.save(*key, state, version)
        ran = 0
        while state.get("pending"):
            if ran == MAX_NODES:
                raise RuntimeError(f"case {key[0]}:{key[1]} ran more than {MAX_NODES} nodes in one run")
            state = merge(state, self.nodes[state["pending"]](state))
            state["pending"] = state.get("next_stage") or None
            version = self.store.save(*key, state, version)
            ran += 1
        return state

    def append(self, config, **lists):
        """Extend the saved state's list fields without running a node: the replies the runtime sent, so
        the next turn reads the whole dialogue."""
        key = _case(config)
        state, version = self.store.load(*key)
        self.store.save(*key, merge(state, lists), version)


def build(hooks, store=None, read_only_tools=frozenset(), now=time.time):
    """The statechart on a case state store (a fresh MemoryStore when none is given)."""
    nodes = {}

    def stage(target, work=lambda state: {}):
        def run(state):
            if not allowed(state.get("stage"), target):
                raise IllegalTransition(f"{state.get('stage')} -> {target.value}")
            return {"next_stage": None, **work(state), "stage": target.value, "path": [target.value]}
        nodes[target.value] = run

    def resume(state):
        """Entry: upgrade the state, then pick where this event takes the case."""
        try:
            update = upgrade(state)
        except UnsupportedSchema as error:
            return {"error": str(error), "next_stage": S.ESCALATED_HITL.value}
        event, current = state.get("event") or {}, state.get("stage")
        kind = event.get("type")
        if kind == "USER_MESSAGE":
            update["messages"] = [{"role": "user", "content": event.get("text", "")}]
        if kind == "USER_MESSAGE" and current in (None, S.RESOLVED.value):
            return {**update, "next_stage": S.INGESTED.value}
        if kind == "USER_MESSAGE" and current == S.AWAITING_CUSTOMER.value:
            return {**update, "next_stage": S.TRIAGED.value}
        if kind == "APPROVAL_GRANTED" and current == S.AWAITING_APPROVAL.value:
            return {**update, "next_stage": S.EXECUTING_SOP.value}
        if kind == "APPROVAL_DENIED" and current == S.AWAITING_APPROVAL.value:
            return {**update, "next_stage": S.RESOLVED.value,
                    "final_response": "The request was not approved."}
        return {**update, "next_stage": None}  # nothing to do, e.g. a human owns the case

    def ingest(state):
        missing = [k for k in ("tenant_id", "case_id", "session_id") if not state.get(k)]
        if missing:
            return {"error": f"ingress is missing {', '.join(missing)}", "next_stage": S.ESCALATED_HITL.value}
        return {"error": None, "final_response": None, "next_stage": S.TRIAGED.value}

    def triage(state):
        result = Triage.model_validate(hooks["triage"](state))
        user_turns = sum(1 for m in episode(state.get("messages") or []) if m.get("role") == "user")
        # ponytail: one clarifying turn; a reply plus a known intent is enough to look up docs (W18 later)
        asked_once = user_turns >= 2 and result.intent != "UNKNOWN" and result.confidence >= 0.50
        if (result.acuity == 3 or result.missing_params) and not asked_once:
            target = S.AWAITING_CUSTOMER
        elif result.confidence < 0.50:
            target = S.ESCALATED_HITL
        elif result.acuity <= 2 and result.confidence >= 0.90:
            target = S.COLLECTING_PARAMS
        elif result.confidence >= 0.85 or asked_once:
            target = S.DELIBERATING  # fallback SOP never executes; it answers from the knowledge base
        else:
            target = S.AWAITING_CUSTOMER
        return {"triage": result.model_dump(), "sop_name": result.sop_name, "parameters": result.parameters,
                "next_stage": target.value}

    def collect(state):
        result = hooks["collect"](state)
        missing = list(result.get("missing", []))
        return {"parameters": result.get("parameters", {}), "missing": missing,
                "sop_name": result.get("sop_name", state.get("sop_name")),
                "next_stage": (S.AWAITING_CUSTOMER if missing else S.EXECUTING_SOP).value}

    def execute_sop(state):
        resumed = state.get("stage") == S.AWAITING_APPROVAL.value
        approved = resumed and (state.get("event") or {}).get("type") == "APPROVAL_GRANTED"
        waited = now() - (state.get("waiting_since") or now())
        if resumed and waited > STALE_AFTER and not hooks["revalidate"](state):
            return {"error": f"state changed during a {waited / 3600:.1f} h wait",
                    "next_stage": S.ESCALATED_HITL.value}
        amount = float((state.get("parameters") or {}).get("amount", 0) or 0)
        if amount >= state.get("approval_threshold", DEFAULT_APPROVAL_THRESHOLD) and not approved:
            return {"needs_approval": True, "next_stage": S.AWAITING_APPROVAL.value}
        result = hooks["execute_sop"](state)
        if result.get("error"):
            return {"error": result["error"], "next_stage": S.ESCALATED_HITL.value}
        return {"needs_approval": False, "final_response": result.get("response"),
                "next_stage": S.RESOLVED.value}

    def deliberate(state):
        episode = state if state.get("stage") == S.DELIBERATING.value else {
            **state, "iterations": 0, "reflexion_trials": 0}
        step = Step.model_validate(hooks["react_step"](episode))
        return {"iterations": 0, "reflexion_trials": 0, **after_step(episode, step, read_only_tools)}

    def wait(state):
        return {"waiting_since": now(), "next_stage": None}

    nodes["resume"] = resume
    stage(S.INGESTED, ingest)
    stage(S.TRIAGED, triage)
    stage(S.COLLECTING_PARAMS, collect)
    stage(S.EXECUTING_SOP, execute_sop)
    stage(S.DELIBERATING, deliberate)
    stage(S.AWAITING_APPROVAL, wait)
    stage(S.AWAITING_CUSTOMER, wait)
    stage(S.RESOLVED)
    stage(S.ESCALATED_HITL)
    return Statechart(nodes, MemoryStore() if store is None else store)
