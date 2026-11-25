"""Gate 1: triage of the customer thread (ADP-05 into the ADP-01 TRIAGED node).

Jev answers the triage question set in one request; code maps intent and severity onto the statechart's
acuity levels. When Jev is down, an LLM classifier answers the same questions, but its confidence is capped
below the 0.90 an SOP needs, so a fallback guess never runs a procedure on its own. If both fail, the result
asks the customer (acuity 3, confidence 0).
"""

import json

from core.orchestrator.decisions import (
    INTENTS,
    SEVERITY,
    TRIAGE_QUESTIONS,
    Answer,
    JevUnavailable,
    checked,
    mask,
)
from schemas.thread_state import episode

SOP_INTENTS = {"BILLING_INQUIRY": "billing_inquiry", "PASSWORD_RESET": "password_reset",
               "SUBSCRIPTION_UPDATE": "subscription_update"}
FALLBACK_MAX_CONFIDENCE = 0.89
ASK_CUSTOMER = {"intent": "UNKNOWN", "acuity": 3, "confidence": 0.0, "missing_params": True,
                "rationale": "no decision available"}


def triage(engine, masked_state, fallback=None):
    """Triage-shaped dict for the statechart (schemas.thread_state.Triage)."""
    source = "jev"
    try:
        answers = engine.ask(masked_state, TRIAGE_QUESTIONS)
    except JevUnavailable:
        if fallback is None:
            return ASK_CUSTOMER
        try:
            answers, source = checked(fallback(masked_state), TRIAGE_QUESTIONS), "fallback"
        except JevUnavailable:
            return ASK_CUSTOMER
    intent, severity = answers["intent"].value, answers["severity"].index  # most likely severity level
    # Route on how sure we are of the intent; severity only sets acuity. A spread over severity levels
    # ("I forgot my password": Low or Significant?) is not doubt about what the customer wants.
    confidence = answers["intent"].confidence
    if source == "fallback":
        confidence = min(confidence, FALLBACK_MAX_CONFIDENCE)
    if intent in SOP_INTENTS:
        acuity = 1 if severity >= 3 else 2
    else:
        acuity = 5 if severity >= 3 else 4
    return {"intent": intent, "acuity": acuity, "confidence": confidence,
            "missing_params": answers["missing_details"].value is True, "sop_name": SOP_INTENTS.get(intent),
            "rationale": f"{source}: {intent}, severity {SEVERITY[severity].split(':')[0]}"}


def llm_fallback(complete):
    """Fallback classifier over `complete(prompt) -> str`, any chat model returning JSON."""
    def classify(masked_state):
        prompt = (
            "Classify the customer conversation. Short replies inherit earlier context. "
            "missing_details is false once the issue is clear enough to look up help "
            "(a named product plus a password or login problem is enough; whether a service, region or "
            "component is having problems, and the customer's own tickets, subscriptions or invoices, can be "
            "looked up). "
            "Reply with JSON only: "
            '{"intent": one of ' + json.dumps(list(INTENTS))
            + ', "severity": index into ' + json.dumps(SEVERITY)
            + ', "missing_details": true or false, "confidence": 0 to 1}\n\nConversation:\n'
            + masked_state["message"].text
        )
        try:
            raw = complete(prompt)
        except Exception as error:  # noqa: BLE001 - a provider outage is "no decision", like a parse error
            raise JevUnavailable(f"fallback classifier unavailable: {error}") from error
        try:
            reply = json.loads(raw)
            confidence = float(reply["confidence"])
            level = int(reply["severity"])
            return {"intent": Answer(reply["intent"], confidence),
                    "severity": Answer(float(level), confidence, level),
                    "missing_details": Answer(reply["missing_details"], confidence)}
        except (ValueError, KeyError, TypeError) as error:
            raise JevUnavailable(f"fallback classifier: {error}") from error
    return classify


def thread_text(messages, limit=8):
    """Role-tagged recent thread for triage; one line is not enough after a clarifying reply."""
    lines = [f"{m.get('role', 'user')}: {m.get('content', '')}" for m in (messages or [])[-limit:]]
    return "\n".join(lines) if lines else ""


def triage_hook(engine, fallback=None):
    """The statechart's `triage(state)` hook: masks the recent thread and triages it."""
    def hook(state):
        # the question being worked on, with its clarifying exchange: an earlier, answered one is not it
        return triage(engine, {"message": mask(thread_text(episode(state.get("messages") or [])))}, fallback)
    return hook
