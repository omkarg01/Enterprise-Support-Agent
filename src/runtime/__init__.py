"""The turn loop: what happens to an accepted turn (M7-01; FR-1, FR-6, FR-9, FR-13, FR-30, FR-32, FR-40).

channels.api accepts a turn (202) and hands it to dispatch(); Dispatcher runs it on a small worker pool, one
turn at a time per conversation, and TurnLoop.run() takes it from there:

1. a status event ("Looking into it");
2. input screening (safety.screening): a blocked message is answered at once and nothing else sees it;
3. PII tokens (safety.pii): from here on only tokenized text reaches Jev, the models or the checkpoint;
4. the statechart (core.orchestrator) on the case's thread, with Jev triage, or the LLM classifier when Jev is
   not configured (its confidence is capped below what an SOP needs);
5. the answer: retrieval (knowledge.retrieval, the public collection for an anonymous customer, plus the
   tenant's own once signed in) and the model router (cost.router, free tiers first) behind the output checks
   (safety.output); a clarifying question when the case waits for the customer; a hand-off notice when it
   goes to a person;
6. citation events for the passages used, then one final event with the AI disclosure, and a decision record
   with its why view (safety.governance).

Any failure publishes an error event: a customer never waits on silence. The case is the conversation for
now (case linking is not wired); procedures (SOPs) have no tools yet, so a procedure turn answers from the
knowledge base and changes nothing. Turns run in the API process, not yet on Temporal: a crash loses the turn
in flight (its checkpoint survives). See the open-issues register.
"""

import logging
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import psycopg

from channels.api import turns as turn_events
from core.context.assembler import delimit
from core.orchestrator.decisions import JevUnavailable, Masked
from core.orchestrator.statechart import build, thread
from core.orchestrator.triage import llm_fallback, triage_hook
from data.events import EventLog
from safety.governance.disclosure import disclose
from safety.output.checks import finalise
from safety.screening.pipeline import SPOTLIGHT_RULE

log = logging.getLogger(__name__)

SYSTEM = ("You are the customer support assistant. Answer only from the knowledge passages given; if "
          "they do not answer the question, say so plainly and offer to bring in a person. Be direct and "
          "concrete, and never promise refunds, credits or changes. " + SPOTLIGHT_RULE)
BLOCKED = "Your message was stopped by our safety checks. A person can help if you need it."
HANDOFF = "A specialist will take it from here. You can keep writing here in the meantime."
FAILED = ("Something went wrong on our side while answering. A person will follow up, or try again in a "
          "moment.")
PROCEDURE = ("No change has been made to your account: describe what the customer can do themselves, and say "
             "a person will carry out anything that needs an account change.")
EVIDENCE = 3


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


class TurnLoop:
    def __init__(self, services):
        self.s = services
        self.evidence = {}             # (tenant, conversation) -> sections used for this turn's answer
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

    def knowledge_answer(self, state, instruction=""):
        latest = Masked(state["messages"][-1]["content"])
        tier = (state.get("event") or {}).get("tier", "T0_ANON")
        sections = self.s.retriever.retrieve(tier, state["tenant_id"], state["messages"][:-1],
                                             latest.text).sections[:EVIDENCE]
        self.evidence[(state["tenant_id"], state["case_id"])] = sections
        passages = "\n\n".join(delimit(f"{s.title}\n{s.text}", f"knowledge {s.doc_id}") for s in sections) \
            or "(no knowledge passage matched)"
        reply = self.s.router.answer(
            [{"role": "system", "content": SYSTEM},
             {"role": "user", "content": f"{passages}\n\n{delimit(latest.text, 'customer message')}\n"
                                         f"{instruction}Answer the customer."}],
            self.s.engine, {"message": latest}, verify=lambda text: self.check(text).verdict == "deliver")
        return reply

    def diagnose(self, state):
        reply = self.knowledge_answer(state)
        if reply.outcome != "answered":
            return {"thought": f"no answer ({reply.outcome})", "outcome": "critique"}
        return {"thought": "answer from the knowledge base", "outcome": "answer", "answer": reply.text}

    def procedure(self, state):
        reply = self.knowledge_answer(state, PROCEDURE + "\n")
        if reply.outcome != "answered":
            return {"error": f"no answer for the procedure ({reply.outcome})"}
        return {"response": reply.text}

    def clarifying_question(self, masked):
        reply = self.s.router.answer(
            [{"role": "system", "content": SYSTEM},
             {"role": "user", "content": f"{delimit(masked, 'customer message')}\nAsk the customer one "
                                         "short, calm question that pins down the problem. Do not guess a "
                                         "diagnosis."}],
            self.s.engine, {"message": Masked(masked)},
            verify=lambda text: self.check(text).verdict == "deliver")
        return reply.text if reply.outcome == "answered" else None

    # One turn ----------------------------------------------------------------------------------------------

    def run(self, turn):
        """Answer one accepted turn (channels.api.envelope.InboundTurn); always ends with a final, handoff or
        error event."""
        key = (turn.tenant_id, turn.conversation_id)
        self.evidence[key] = []
        try:
            self.publish(turn, "status", {"state": "working", "text": "Looking into it"})
            verdict = self.s.screener.screen("user_message", turn.content)
            if verdict.band == "block":
                self.record(turn, "block", "Your message was stopped by our safety checks.",
                            ["Content safety"],
                            {"screening": {"score": verdict.score, "categories": verdict.categories}})
                self.publish(turn, "final", disclose({"role": "assistant", "text": BLOCKED}))
                return "blocked"
            masked, _ = self.s.tokens.mask(turn.content)
            state = self.graph.invoke(
                {"tenant_id": turn.tenant_id, "case_id": turn.conversation_id,
                 "session_id": turn.principal_id,
                 "event": {"type": "USER_MESSAGE", "text": masked, "tier": turn.assurance_tier}},
                thread(turn.tenant_id, turn.conversation_id))
            trace = {"triage": state.get("triage"), "path": state.get("path")}
            if state.get("stage") == "RESOLVED" and state.get("final_response"):
                text = self.check(state["final_response"]).text
                for section in self.evidence.get(key, []):
                    self.publish(turn, "citation", {"doc_id": section.doc_id, "title": section.title})
                self.record(turn, "answer", "We answered from our documentation.", ["Content safety"], trace)
                self.publish(turn, "final", disclose({"role": "assistant", "text": text}))
                return "answered"
            if state.get("stage") == "AWAITING_CUSTOMER":
                question = self.clarifying_question(masked)
                if question:
                    self.record(turn, "ask_customer", "We need one more detail before we can help.",
                                ["Content safety"], trace)
                    self.publish(turn, "final", disclose({"role": "assistant", "text": question}))
                    return "asked"
            self.record(turn, "escalate", "A specialist will look at this with you.", [], trace)
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
            self.evidence.pop(key, None)


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
