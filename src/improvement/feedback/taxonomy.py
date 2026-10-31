"""The failure taxonomy (NFR-32 / CI-ADP-02: CI-D2): the one code-versioned vocabulary of failures, owned
here.

Two levels: the subsystem (triage, retrieval, multi_agent, tools, safety, gate, infrastructure, experience)
and the failure within it, as "subsystem.failure". Each node has the description Jev reads when it classifies
free text (no digits: the Jev rule), its epistemic quadrant (KK, KU, UK, UU) and a severity tier: 4 critical
(money moved without approval, personal data disclosed, an authorization escape), 2 a behavioural defect,
1 a superficial flaw. UNCLASSIFIED collects what fits nowhere, for the monthly taxonomy review.

The runtime analyzer's categories (observability.alerts.Failure) and the specialists' reason codes
(hitl.handoff.DraftFailure, hitl.approval.RejectReason) map into it, so every source lands on one tree.
"""

from dataclasses import dataclass

VERSION = "2026-10-31.1"
TIER_WEIGHT = {4: 100, 2: 10, 1: 1}
UNCLASSIFIED = "unclassified_anomaly"


@dataclass(frozen=True)
class Node:
    id: str
    description: str
    quadrant: str          # KK, KU, UK or UU
    tier: int              # 4, 2 or 1
    postmortem: bool = False

    @property
    def subsystem(self):
        return self.id.split(".", 1)[0]

    @property
    def weight(self):
        return TIER_WEIGHT[self.tier]


TREE = {node.id: node for node in (
    Node("triage.misclassified_intent", "The request was understood as a different kind of problem", "KU", 2),
    Node("triage.missed_human_request", "The customer asked for a person and was not handed over", "KU", 2),
    Node("retrieval.knowledge_gap", "The documentation needed to answer was missing or not found", "KU", 2),
    Node("retrieval.stale_document", "The answer came from outdated documentation", "UK", 2),
    Node("retrieval.irrelevant_passage", "The cited passages did not match the question", "KU", 1),
    Node("multi_agent.wrong_specialist", "The case was sent to the wrong specialist or domain", "KU", 2),
    Node("multi_agent.contradictory_claims", "Specialists gave conflicting facts or figures", "KU", 2),
    Node("multi_agent.loop_limit", "The agents ran out of steps without an answer", "KK", 1),
    Node("tools.argument_error", "A tool was called with wrong or invented arguments", "KU", 2),
    Node("tools.wrong_target", "An action was aimed at the wrong account, record or amount", "KU", 4,
         postmortem=True),
    Node("tools.unapproved_financial_write", "Money moved or a charge changed without the required approval",
         "UU", 4, postmortem=True),
    Node("safety.injection_attempt", "Someone tried to override the agent's instructions and was stopped",
         "KK", 1),
    Node("safety.jailbreak", "The agent was manipulated into ignoring its rules or limits", "UU", 4,
         postmortem=True),
    Node("safety.pii_disclosure", "Personal or confidential data was shown to someone who should not see it",
         "UU", 4, postmortem=True),
    Node("safety.authorization_escape", "An action ran that the user was not allowed to take", "UU", 4,
         postmortem=True),
    Node("gate.hallucination", "The reply stated something the evidence does not support", "UK", 2),
    Node("gate.miscalculation", "A number, total or date in the reply was wrong", "KU", 2),
    Node("gate.unbacked_promise", "The reply promised a refund, credit or outcome nobody had done", "KU", 2),
    Node("gate.tone_or_policy", "The reply was rude, off-policy or confusing", "KK", 1),
    Node("infrastructure.system_down", "A connected system was down or unreachable", "KK", 1),
    Node("infrastructure.timeout", "The answer took too long or timed out", "KK", 1),
    Node("infrastructure.permission_denied", "A system refused the agent access it needed", "KK", 1),
    Node("experience.repetitive", "The agent repeated itself or went in circles", "KU", 1),
    Node("experience.out_of_scope", "The customer asked for something the agent does not support", "KK", 1),
    Node("experience.frustration", "The customer was frustrated by effort, waiting or being passed around",
         "KU", 1),
    Node(UNCLASSIFIED, "None of the other descriptions fit", "UU", 2),
)}

# Every other source of failure codes, onto the tree.
FROM_ANALYZER = {  # observability.alerts.Failure values
    "infrastructure_external_system_down": "infrastructure.system_down",
    "permission_authorization_denial": "infrastructure.permission_denied",
    "orchestration_agent_loop_limit": "multi_agent.loop_limit",
    "infrastructure_timeout_abort": "infrastructure.timeout",
    "safety_adversarial_injection_blocked": "safety.injection_attempt",
    "incorrect_specialist_routing": "multi_agent.wrong_specialist",
    "retrieval_knowledge_gap": "retrieval.knowledge_gap",
    "unbacked_promise_attempt": "gate.unbacked_promise",
    "customer_sentiment_escalation": "experience.frustration",
    "unsupported_out_of_scope_request": "experience.out_of_scope",
    "repetitive_circular_reasoning": "experience.repetitive",
}
FROM_DRAFT = {  # hitl.handoff.DraftFailure values
    "grounding_hallucination": "gate.hallucination",
    "retrieval_irrelevance": "retrieval.irrelevant_passage",
    "numeric_miscalculation": "gate.miscalculation",
    "tone_or_policy_violation": "gate.tone_or_policy",
    "tool_argument_error": "tools.argument_error",
}
FROM_REJECTION = {  # hitl.approval.RejectReason values: what a specialist stopped before it ran
    "mismatched_amount": "tools.wrong_target",
    "wrong_account": "tools.wrong_target",
    "unauthorized_user": "safety.authorization_escape",
    "suspected_fraud": "safety.jailbreak",
    "policy_violation": "gate.tone_or_policy",
    "state_changed": "tools.argument_error",
}


def node(code):
    """The node for a taxonomy id, or for a code from any mapped source."""
    for table in (FROM_ANALYZER, FROM_DRAFT, FROM_REJECTION):
        if code in table:
            return TREE[table[code]]
    if code not in TREE:
        raise KeyError(f"{code!r} is not in taxonomy {VERSION}")
    return TREE[code]
