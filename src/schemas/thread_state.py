"""State of one case's orchestrator thread (ADP-01 state schema; MS-ADP-04 MS-D8, MS-D13).

The case state store (core.orchestrator.case_state, DECISION-bc6301b4) keeps this state and is the single
source of truth (MS-D8); merge() applies a node's update. Values are plain JSON (no custom classes) so any
saved state can be read back. Every state carries schema_version; a state
from an older version is upgraded through MIGRATIONS, and one with no migration path goes to a human (MS-D13).
"""

import operator
from collections.abc import Callable
from enum import Enum
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1


class Stage(str, Enum):
    INGESTED = "INGESTED"
    TRIAGED = "TRIAGED"
    COLLECTING_PARAMS = "COLLECTING_PARAMS"
    EXECUTING_SOP = "EXECUTING_SOP"
    DELIBERATING = "DELIBERATING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    AWAITING_CUSTOMER = "AWAITING_CUSTOMER"
    RESOLVED = "RESOLVED"
    ESCALATED_HITL = "ESCALATED_HITL"


class Triage(BaseModel):
    """What the triage decision (Jev, ADP-05) returns for one message."""

    intent: str
    acuity: int = Field(ge=1, le=5)  # 1-2 SOP, 3 ambiguous, 4-5 diagnostic
    confidence: float = Field(ge=0.0, le=1.0)
    missing_params: bool = False
    sop_name: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""


class Step(BaseModel):
    """One scoped-ReAct step: thought, optional read-only action and its observation, and the outcome."""

    thought: str
    action: str | None = None
    action_input: dict[str, Any] = Field(default_factory=dict)
    observation: str | None = None
    outcome: Literal["continue", "answer", "needs_sop", "critique"] = "continue"
    answer: str | None = None
    sop_name: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class ThreadState(TypedDict, total=False):
    schema_version: int
    tenant_id: str
    case_id: str
    session_id: str
    event: dict[str, Any]                            # what started this run: a message or an approval outcome
    stage: str | None
    path: Annotated[list[str], operator.add]         # every stage entered, in order (audit)
    messages: Annotated[list[dict[str, str]], operator.add]
    triage: dict[str, Any] | None
    sop_name: str | None
    parameters: dict[str, Any]
    missing: list[str]
    approval_threshold: float
    needs_approval: bool
    waiting_since: float | None                      # epoch seconds when the run suspended
    trajectory: Annotated[list[dict[str, Any]], operator.add]
    iterations: int
    reflexion_trials: int
    next_stage: str | None                           # routing decision of the last node
    error: str | None
    final_response: str | None


APPEND = ("path", "messages", "trajectory")  # the list fields above that grow; every other field is replaced


def episode(messages):
    """The messages of the question being worked on: everything after the assistant's last answer. A
    clarifying question ({"role": "assistant", "kind": "question"}) does not end one, so the customer's reply
    stays with what it answers. A thread with no answer recorded (one from before replies were kept) is all
    one episode."""
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "assistant" and messages[i].get("kind") != "question":
            return messages[i + 1:]
    return list(messages)


def merge(state, update):
    """`state` with a node's `update` applied: APPEND fields are extended, every other field is replaced."""
    merged = dict(state)
    for name, value in update.items():
        merged[name] = [*(state.get(name) or []), *value] if name in APPEND else value
    return merged


class UnsupportedSchema(ValueError):
    pass


# version v -> function returning the updates that turn a v state into a v + 1 state (never list fields).
MIGRATIONS: dict[int, Callable[[dict], dict]] = {}


def upgrade(state):
    """Updates that bring `state` to SCHEMA_VERSION; raises UnsupportedSchema if there is no path."""
    version = state.get("schema_version", SCHEMA_VERSION)
    updates = {}
    while version < SCHEMA_VERSION and version in MIGRATIONS:
        updates.update(MIGRATIONS[version]({**state, **updates}))
        version += 1
    if version != SCHEMA_VERSION:
        raise UnsupportedSchema(f"no migration from schema version {state.get('schema_version')}")
    return {**updates, "schema_version": SCHEMA_VERSION}
