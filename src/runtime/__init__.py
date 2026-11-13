"""The turn loop: what happens to an accepted turn (M7-01; FR-1, FR-6, FR-9, FR-13, FR-30, FR-32, FR-40).

channels.api accepts a turn (202) and hands it to dispatch(); Dispatcher runs it on a small worker pool, one
turn at a time per conversation, and TurnLoop.run() takes it from there:

1. a status event ("Looking into it");
2. input screening (safety.screening): a blocked message is answered at once and nothing else sees it;
3. PII tokens (safety.pii): from here on only tokenized text reaches Jev, the models or the checkpoint;
4. the statechart (core.orchestrator) on the case's thread, with Jev triage, or the LLM classifier when Jev is
   not configured (its confidence is capped below what an SOP needs);
5. the answer: retrieval once per turn (knowledge.retrieval, the public collection for an anonymous customer,
   plus the tenant's own once signed in: customer_visible passages and the internal runbooks, which are read
   but never cited), then the specialists (agents.topology): Jev picks a lead and any co-specialists, each
   may first read one of the customer's own records (its read-only tools.nimbus tools, at the turn's tier,
   through the audited executor; the output is tokenized and is evidence too), reads the passages for its
   domain and returns typed claims, and agents.merge checks them and has the
   coordinator draft the one reply through the model router (cost.router, free tiers first) behind the
   output checks (safety.output). Without Jev the generalist answers alone, with every tool. No claim means
   the documentation
   does not cover it, and the case goes to a person. A clarifying question when the case waits for the
   customer; a hand-off notice when it goes to a person;
6. citation events for the customer_visible passages the accepted claims cite, then one final event with the
   AI disclosure, and a decision record with its why view (safety.governance) and the steps the turn took,
   which channels.api.trace shows the customer as a tier-filtered "how this was answered" view.

Any failure publishes an error event: a customer never waits on silence. The case is the conversation for
now (case linking is not wired); procedures (SOPs) have no tools yet, so a procedure turn answers from the
knowledge base and changes nothing. Turns run in the API process, not yet on Temporal: a crash loses the turn
in flight (its checkpoint survives). See the open-issues register.
"""

import json
import logging
import re
import threading
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import psycopg

from agents.merge import COMPLETED, Claim, Outcome, claims_from, human, merge
from agents.topology import (
    MAX_FINDINGS,
    STEPS_PER_SPECIALIST,
    Role,
    Specialist,
    coordinator,
    executor_tool,
    registry,
    specialist_graph,
)
from channels.api import turns as turn_events
from channels.identity.tiers import Tier
from core.context.assembler import delimit
from core.orchestrator.decisions import JevUnavailable, Masked, mask
from core.orchestrator.statechart import build, thread
from core.orchestrator.triage import llm_fallback, triage_hook
from data.events import EventLog
from safety.governance.disclosure import disclose
from safety.output.checks import finalise
from safety.screening.pipeline import SPOTLIGHT_RULE
from tools.registry import Registry

log = logging.getLogger(__name__)

SYSTEM = ("You are the customer support assistant. Answer only from the knowledge given (passages or "
          "specialist findings); if it does not answer the question, say so plainly and offer to bring in a "
          "person. Be direct and concrete, and never promise refunds, credits or changes. " + SPOTLIGHT_RULE)
BLOCKED = "Your message was stopped by our safety checks. A person can help if you need it."
HANDOFF = "A specialist will take it from here. You can keep writing here in the meantime."
FAILED = ("Something went wrong on our side while answering. A person will follow up, or try again in a "
          "moment.")
PROCEDURE = ("No change has been made to your account: describe what the customer can do themselves, and say "
             "a person will carry out anything that needs an account change.")
NO_EVIDENCE = "Our documentation does not cover this, so a specialist will look at it with you."
EVIDENCE = 5                       # passages the specialists read
MAX_CLAIMS = 8
CUSTOMER = ("customer_visible",)   # a customer's answer cites only these chunks (S18)
UNCITED = ("runbooks",)            # also read for a customer's answer, never cited: the internal runbooks
TEAM = {  # what Jev reads to pick the lead (no digits: the Jev rule)
    Role.GENERALIST: "General product questions and how-to guidance that fit no other specialist",
    Role.BILLING: "Invoices, charges, payments, refunds, credits, plan pricing and service level credits",
    Role.TECHNICAL: ("Outages, errors and failing services: networking and VPN, databases, containers, APIs, "
                     "webhooks, infrastructure as code and point of sale terminals"),
    Role.ACCOUNT_OPS: ("Sign-in, passwords, multi-factor authentication, single sign-on, identity "
                       "verification, users and roles, subscriptions and data privacy"),
}
TOOLS = {  # each specialist's read-only tools (tools.nimbus, W41); without Jev the generalist has all
    Role.GENERALIST: ("nimbus_status.service_status",),
    Role.BILLING: ("nimbus_account.my_invoices", "nimbus_support.my_subscriptions"),
    Role.TECHNICAL: ("nimbus_status.service_status", "nimbus_support.my_tickets"),
    Role.ACCOUNT_OPS: ("nimbus_account.my_account", "nimbus_support.my_subscriptions"),
}
FINDINGS = """You are the {role} specialist of the support team ({about}).
From the knowledge passages and customer records above, list the facts and steps that answer the customer's
message within your domain. Passages labelled internal are staff runbooks: keep only what the customer can do
or should know, never internal tools, teams, document names or steps only staff can perform. Reply with JSON
only, at most eight items:
[{{"topic": "short_snake_case name unique to this item", "text": "one fact or step, with its exact commands",
"evidence": ["<passage or record id>"]}}]
Add "value" (a number) and "unit" only when the fact is a quantity. Reply [] when nothing in the passages
or records answers it."""
TOOL_STEP = """Before answering you may read one of the customer's own records, if it would help:
{tools}
To read one, reply with JSON only: {{"action": "<record id>"}}. Otherwise reply with the findings as asked."""
DRAFT = "Answer the customer from the specialist findings only."
FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


class NoDraft(RuntimeError):
    """The coordinator's draft did not pass the router (no model answered, or the checks held it twice)."""


class Remasked:
    """The decision engine behind decisions.mask(): its personal-data guard reads dates and long numbers as
    phone numbers, which the token vault leaves alone, so text is masked its way before Jev is asked."""

    def __init__(self, engine):
        self.engine = engine

    def ask(self, state, questions):
        return self.engine.ask({k: mask(v.text) if isinstance(v, Masked) else v for k, v in state.items()},
                               questions)


def findings(text, ids):
    """A specialist's reply as the findings JSON agents.merge reads, or None: at most eight valid claims, each
    with its own topic, citing only passages it was given."""
    try:
        items = json.loads(FENCE.sub("", (text or "").strip()))
        claims = [Claim(**item) for item in items] if isinstance(items, list) else None
    except (TypeError, ValueError):  # pydantic's ValidationError is a ValueError
        return None
    if claims is None or len(claims) > MAX_CLAIMS or len({c.topic for c in claims}) != len(claims) \
            or any(set(c.evidence) - ids for c in claims):
        return None
    text = json.dumps(items)
    return text if len(text) <= MAX_FINDINGS else None


def chosen(text, offered):
    """The record a specialist asked to read ({"action": id} naming one it was offered), or None."""
    try:
        move = json.loads(FENCE.sub("", (text or "").strip()))
    except ValueError:
        return None
    return move["action"] if isinstance(move, dict) and move.get("action") in offered else None


def passages(sections):
    """The passages as a model reads them; a passage's label is its id, which claims cite."""
    def label(section):
        return f"{'knowledge' if section.audience in CUSTOMER else 'internal knowledge'} {section.parent_id}"
    return "\n\n".join(delimit(f"{s.title}\n{s.text}", label(s)) for s in sections) \
        or "(no knowledge passage matched)"


class NoJev:
    """Jev is not configured: every decision fails safe (triage falls back to the LLM classifier)."""

    def evaluate(self, state, questions):
        raise JevUnavailable("no Jev API key configured")


@dataclass
class Services:
    engine: object                 # core.orchestrator.decisions.DecisionEngine
    router: object                 # cost.router.Router
    retriever: object              # knowledge.retrieval.pipeline.Retriever
    screener: object               # safety.screening.pipeline.Screener
    tokens: object                 # safety.pii.tokens.Tokenizer
    complete: object               # prompt -> text, a small model (triage fallback)
    connect: object                # () -> autocommit psycopg connection to the main database
    checkpointer: object           # a LangGraph checkpointer
    decisions: object = None       # safety.governance.records.DecisionLog
    executor: object = None        # tools.execution.Executor over `tools`, audited; None: no tools
    tools: object = None           # the frozen tool registry (tools.nimbus.catalog)


class TurnLoop:
    def __init__(self, services):
        self.s = services
        self.evidence = {}             # (tenant, conversation) -> sections this turn's answer cites
        self.passages = {}             # (tenant, conversation) -> sections the specialists read this turn
        self.steps = {}                # (tenant, conversation) -> retrieval and answer steps, for the trace
        self.turns = {}                # (tenant, conversation) -> this turn's session, principal, tier, id
        self.records = {}              # (tenant, conversation) -> {tool id: masked output} read this turn
        self.decide = Remasked(services.engine)
        tooled = services.executor is not None and services.tools is not None
        self.catalog = services.tools if tooled else Registry().freeze()
        self.team = registry([Specialist(role, about, self.allowed(TOOLS[role]), self.specialist(role))
                              for role, about in TEAM.items()], self.catalog)
        # no checkpointer of its own: run inside a statechart node, it is checkpointed with the case; each
        # turn passes its run_tool in the config (consult)
        self.coordinator = coordinator(self.decide, self.team, run_tool=None)
        everything = self.allowed(self.catalog.tools)
        self.generalist = specialist_graph(Specialist(Role.GENERALIST, "every topic", everything,
                                                      self.specialist(Role.GENERALIST, "every topic")), None)
        self.graph = build({"triage": triage_hook(services.engine, llm_fallback(services.complete)),
                            "collect": lambda state: {"parameters": state.get("parameters") or {},
                                                      "missing": []},
                            "execute_sop": self.procedure, "react_step": self.diagnose,
                            "revalidate": lambda state: True}, checkpointer=services.checkpointer)

    # Publishing --------------------------------------------------------------------------------------------

    def publish(self, turn, event_type, data):
        with self.s.connect() as conn:
            turn_events.publish(EventLog(conn), turn.tenant_id, turn.conversation_id, event_type,
                                {"turn_id": turn.turn_id, **data})

    def record(self, turn, decision, reason, checks, trace):
        if self.s.decisions is None:
            return
        try:
            self.s.decisions.record(turn.tenant_id, turn.conversation_id, decision, reason, checks, trace,
                                    turn_id=turn.turn_id)
        except Exception:  # noqa: BLE001 - a missing audit row must not cost the customer their reply
            log.exception("decision record failed for turn %s", turn.turn_id)

    # The answer --------------------------------------------------------------------------------------------

    def check(self, draft):
        return finalise(draft, lambda instruction: draft, system_prompt=SYSTEM)

    def customer_query(self, messages):
        texts = [m.get("content", "") for m in messages if m.get("role") == "user"]
        return " ".join(texts[-4:]) or (messages[-1]["content"] if messages else "")

    def retrieve(self, state, query):
        """This turn's passages: customer_visible ones plus the internal runbooks, which are never cited."""
        key, failed = (state["tenant_id"], state["case_id"]), False
        tier = (state.get("event") or {}).get("tier", "T0_ANON")
        try:
            result = self.s.retriever.retrieve(tier, state["tenant_id"], state["messages"][:-1], query,
                                               audiences=CUSTOMER, sources=UNCITED)
            sections, query = result.sections[:EVIDENCE], result.query.text
        except Exception:  # noqa: BLE001 - the embedder or index is down: no passages, so no claims
            log.warning("retrieval failed; the specialists read no knowledge passages", exc_info=True)
            sections, failed = [], True
        self.passages[key] = sections
        self.steps.setdefault(key, {})["retrieval"] = {
            "query": query, "failed": failed, "collections": sorted({s.collection for s in sections}),
            "sources": [], "internal": sum(s.audience not in CUSTOMER for s in sections)}
        return sections

    def allowed(self, tool_ids):
        return frozenset(t for t in tool_ids if t in self.catalog.tools)

    def offered(self, key, tools):
        """What a specialist may read this turn: its own tools, at the turn's tier, with a live session."""
        turn = self.turns.get(key) or {}
        if not turn.get("session_id"):
            return []
        tier = Tier[turn["tier"]]
        return sorted(t for t in tools if tier >= self.catalog.tools[t].min_tier)

    def tool_runner(self, key):
        """run_tool for this turn's specialists: the audited executor, its output tokenized (safety.pii)
        before any model reads it and kept as evidence for agents.merge."""
        if self.s.executor is None:
            return None
        run = executor_tool(self.s.executor)

        def masked_run(request):
            text = self.s.tokens.mask(run(request))[0]
            self.records.setdefault(key, {})[request.tool_id] = text
            self.steps.setdefault(key, {}).setdefault("tools", []).append(
                {"role": request.role, "tool": request.tool_id})
            return text
        return masked_run

    def specialist(self, role, about=None):
        """A specialist's step (agents.topology act): on its first step it may ask to read one of the
        customer's records (a tool it is offered); then it reads this turn's passages and what it read, and
        reports typed claims; [] when none of them answers it, or when no model gave valid findings."""
        def act(brief, history):
            key = (brief["tenant_id"], brief["case_id"])
            sections = self.passages.get(key, [])
            read = [h["action"] for h in history if "ok" in (h.get("observation") or {})]
            records = "".join(f"\n\n{self.records[key][t]}" for t in read)
            tools = self.catalog.tools if about else self.team[role].tools  # about: the generalist alone
            offered = [] if history else self.offered(key, tools)
            upstream = brief.get("upstream")
            lead = delimit(upstream["findings"], f"{upstream['role']} findings") + "\n" if upstream else ""
            choice = ("\n" + TOOL_STEP.format(tools="\n".join(f"- {t}: {self.catalog.tools[t].description}"
                                                               for t in offered))) if offered else ""
            task = FINDINGS.format(role=role.value.replace("_", " "), about=about or TEAM[role])
            prompt = (f"{passages(sections)}{records}\n\n{delimit(brief['objective'], 'customer message')}\n"
                      f"{lead}{task}{choice}")
            ids = {s.parent_id for s in sections} | set(read)
            reply = self.s.router.answer([{"role": "system", "content": SPOTLIGHT_RULE},
                                          {"role": "user", "content": prompt}],
                                         self.decide, {"message": Masked(brief["objective"])},
                                         verify=lambda text: findings(text, ids) is not None
                                         or chosen(text, offered) is not None)
            action = chosen(reply.text, offered) if reply.outcome == "answered" else None
            if action:
                return {"thought": f"read {action}", "action": action, "action_input": {}}
            found = (reply.outcome == "answered" and findings(reply.text, ids)) or "[]"
            claims = json.loads(found)
            self.steps.setdefault(key, {}).setdefault("specialists", []).append(
                {"role": role.value, "outcome": reply.outcome, "claims": len(claims)})
            return {"thought": f"read the passages as the {role.value} specialist", "findings": found,
                    "evidence": sorted({e for claim in claims for e in claim["evidence"]})}
        return act

    def draft(self, state, instruction):
        """agents.merge's draft(claims): the coordinator, the only author of customer text, writes one
        reply."""
        key, messages = (state["tenant_id"], state["case_id"]), state["messages"]

        def write(claims):
            facts = "\n".join(f"- {claim.text}" for claim in claims)
            conversation = "\n".join(f"{m.get('role', 'user')}: {m.get('content', '')}"
                                     for m in messages[-8:])
            content = (f"{delimit(facts, 'specialist findings')}\n\n{delimit(conversation, 'conversation')}\n"
                       f"{instruction}{DRAFT}")
            reply = self.s.router.answer(
                [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}],
                self.decide, {"message": Masked(self.customer_query(messages))},
                verify=lambda text: self.check(text).verdict == "deliver")
            self.steps.setdefault(key, {})["answer"] = {"outcome": reply.outcome, "retried": reply.cascaded}
            if reply.outcome != "answered":
                raise NoDraft(f"NO_DRAFT_{reply.outcome.upper()}")
            return reply.text
        return write

    def consult(self, state, instruction=""):
        """Retrieval, the specialists and the merge: (reply text, "answered"), or (None, why a person takes
        it: NOTHING_TO_SAY when no passage answers it, or an agents.merge / delegation code)."""
        key, query = (state["tenant_id"], state["case_id"]), self.customer_query(state["messages"])
        sections, draft = self.retrieve(state, query), self.draft(state, instruction)
        evidence = {s.parent_id: f"{s.title}\n{s.text}" for s in sections}
        who = self.turns.get(key) or {}
        brief = {"tenant_id": key[0], "case_id": key[1], "turn_id": who.get("turn_id") or str(uuid.uuid4()),
                 # no session (a turn from before AGENT-02): offered() offers no tool, so it is never used
                 "session_id": who.get("session_id") or "none",
                 "principal_id": who.get("principal_id") or state["session_id"]}
        config = {"configurable": {"run_tool": self.tool_runner(key)}}
        try:
            if isinstance(getattr(self.s.engine, "jev", None), NoJev):
                outcome = self.alone(brief, query, draft, config)
            else:
                turn = self.coordinator.invoke({**brief, "message": query}, config)
                outcome = human("NO_DELEGATION") if turn["plan"]["handoff"] else merge(
                    self.decide, turn["results"], {**evidence, **self.records.get(key, {})}, draft)
        except NoDraft as error:
            outcome = human(str(error))
        used = {e for claim in outcome.accepted for e in claim.evidence}
        self.evidence[key] = [s for s in sections if s.parent_id in used and s.audience in CUSTOMER]
        steps = self.steps.setdefault(key, {})
        steps["retrieval"]["sources"] = [{"doc_id": s.doc_id, "title": s.title} for s in self.evidence[key]]
        steps.setdefault("answer", {"retried": False})["outcome"] = (
            "answered" if outcome.kind == "reply" else outcome.reason)
        return (outcome.text, "answered") if outcome.kind == "reply" else (None, outcome.reason)

    def alone(self, brief, query, draft, config):
        """Without Jev nothing delegates or adjudicates: the generalist reads for every domain, with every
        tool; agents.merge's code-only check still applies (topics are unique per specialist: nothing to
        adjudicate)."""
        done = self.generalist.invoke({"brief": {**brief, "role": Role.GENERALIST.value, "objective": query,
                                                 "steps": STEPS_PER_SPECIALIST}, "history": []}, config)
        claims = tuple(claims_from(done["result"]))
        if not claims:
            return human("NOTHING_TO_SAY")
        text = draft(claims)
        if COMPLETED.search(text):
            return human("UNVERIFIED_COMPLETION")
        return Outcome("reply", text, accepted=claims)

    def diagnose(self, state):
        text, why = self.consult(state)
        return {"thought": f"the specialists' answer ({why})", "outcome": "answer", "answer": text}

    def procedure(self, state):
        text, why = self.consult(state, PROCEDURE + "\n")
        return {"response": text} if text else {"error": f"no answer for the procedure ({why})"}

    def clarifying_question(self, state, masked):
        thread = "\n".join(f"{m.get('role', 'user')}: {m.get('content', '')}"
                           for m in (state.get("messages") or [])[-8:]) or masked
        reply = self.s.router.answer(
            [{"role": "system", "content": SYSTEM},
             {"role": "user", "content": f"{delimit(thread, 'conversation')}\nAsk the customer one short, "
                                         "calm question that is not already answered in the conversation. "
                                         "Do not guess a diagnosis."}],
            self.decide, {"message": Masked(masked)},
            verify=lambda text: self.check(text).verdict == "deliver")
        return reply.text if reply.outcome == "answered" else None

    # One turn ----------------------------------------------------------------------------------------------

    def run(self, turn):
        """Answer one accepted turn (channels.api.envelope.InboundTurn); always ends with a final, handoff or
        error event."""
        key = (turn.tenant_id, turn.conversation_id)
        self.evidence[key], self.passages[key], self.steps[key] = [], [], {}
        self.turns[key] = {"session_id": turn.session_id, "principal_id": turn.principal_id,
                           "tier": turn.assurance_tier, "turn_id": turn.turn_id}
        try:
            self.publish(turn, "status", {"state": "working", "text": "Looking into it"})
            verdict = self.s.screener.screen("user_message", turn.content)
            steps = {"screening": verdict.band}
            if verdict.band == "block":
                self.record(turn, "block", "Your message was stopped by our safety checks.",
                            ["Content safety"],
                            {"screening": {"score": verdict.score, "categories": verdict.categories},
                             "steps": steps})
                self.publish(turn, "final", disclose({"role": "assistant", "text": BLOCKED}))
                return "blocked"
            masked, found = self.s.tokens.mask(turn.content)
            config = thread(turn.tenant_id, turn.conversation_id)
            seen = len(self.graph.get_state(config).values.get("path") or [])  # path spans the whole case
            state = self.graph.invoke(
                {"tenant_id": turn.tenant_id, "case_id": turn.conversation_id,
                 # the statechart only checks one is there; tools take the live one from self.turns (B22)
                 "session_id": turn.session_id or turn.principal_id,
                 "event": {"type": "USER_MESSAGE", "text": masked, "tier": turn.assurance_tier}}, config)
            triage = state.get("triage") or {}
            steps.update(self.steps.get(key, {}), masked=bool(found), stages=(state.get("path") or [])[seen:],
                         intent=triage.get("intent"),
                         classifier=str(triage.get("rationale") or "").split(":", 1)[0] or None)
            trace = {"triage": state.get("triage"), "path": state.get("path"), "steps": steps}
            if state.get("stage") == "RESOLVED" and state.get("final_response"):
                checked = self.check(state["final_response"])
                steps["review"] = checked.verdict
                for section in self.evidence.get(key, []):
                    self.publish(turn, "citation", {"doc_id": section.doc_id, "title": section.title,
                                                    "heading": section.heading,
                                                    "excerpt": section.text[:600]})
                self.record(turn, "answer", "We answered from our documentation.", ["Content safety"], trace)
                self.publish(turn, "final", disclose({"role": "assistant", "text": checked.text}))
                return "answered"
            if state.get("stage") == "AWAITING_CUSTOMER":
                question = self.clarifying_question(state, masked)
                if question:
                    self.record(turn, "ask_customer", "We need one more detail before we can help.",
                                ["Content safety"], trace)
                    self.publish(turn, "final", disclose({"role": "assistant", "text": question}))
                    return "asked"
            uncovered = steps.get("answer", {}).get("outcome") == "NOTHING_TO_SAY"
            self.record(turn, "escalate",
                        NO_EVIDENCE if uncovered else "A specialist will look at this with you.", [], trace)
            self.publish(turn, "handoff", {"text": HANDOFF})
            return "handed_off"
        except Exception:  # noqa: BLE001 - the customer gets an answer whatever broke
            log.exception("turn %s failed", turn.turn_id)
            try:
                self.publish(turn, "error", {"text": FAILED})
            except Exception:  # noqa: BLE001 - the event log itself is down: nothing more to tell anyone
                log.exception("could not publish the error for turn %s", turn.turn_id)
            return "failed"
        finally:
            for per_turn in (self.evidence, self.passages, self.steps, self.turns, self.records):
                per_turn.pop(key, None)


class Dispatcher:
    """channels.api's dispatch hook: runs accepted turns on a worker pool, one at a time per conversation."""

    def __init__(self, loop, workers=4):
        self.loop, self.pool = loop, ThreadPoolExecutor(max_workers=workers, thread_name_prefix="turn")
        self.locks, self.guard = defaultdict(threading.Lock), threading.Lock()

    def _run(self, turn):
        with self.guard:
            lock = self.locks[(turn.tenant_id, turn.conversation_id)]
        with lock:
            return self.loop.run(turn)

    def __call__(self, turn):
        return self.pool.submit(self._run, turn)


def connector(dsn):
    def connect():
        return psycopg.connect(dsn, autocommit=True)
    return connect
