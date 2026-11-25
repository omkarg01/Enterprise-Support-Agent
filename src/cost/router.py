"""Model tier routing (FR-40 / CR-ADP-01: CR-D1, CR-D2, CR-D9) on the v1 learning profile.

Tiers, cheapest first: 0 a small free model (Groq / Ollama), 1 a stronger free model (Gemini / Groq), 2 Claude
under the USD 10 monthly cap. A Jev difficulty score picks the tier (below 0.35 -> 0, below 0.75 -> 1, else 2;
mid when Jev cannot answer). A reply that fails its checks is re-run once, one tier up; a tier where no
provider answers is re-run one tier up when that tier adds a provider not yet tried (Claude, within the
cap); failing again goes to a human (or reads as degraded when no provider answered at all).
Each provider failure is logged with its code (register B23). Within a tier, providers fall back to free
ones (never to a paid one), and Claude is skipped when the cap cannot cover the turn, so the hardest turns
land on the best free model.
"""

import logging
from dataclasses import dataclass

from core.context.assembler import approx_tokens
from core.orchestrator.decisions import JevUnavailable
from reliability.fallbacks import AllProvidersFailed, ProviderError, RetryBudget, call_chain

log = logging.getLogger(__name__)

THETA_LOW, THETA_HIGH = 0.35, 0.75
MONTHLY_CAP_USD = 10.0
DIFFICULTY = {"difficulty": {
    "type": "score",
    "instructions": "How hard is it to answer this request correctly and completely?",
    "criteria": ["Trivial: a greeting or a fact stated in one place",
                 "Easy: one known answer from one source",
                 "Moderate: combines a few facts or steps",
                 "Hard: multi-step reasoning, diagnosis or a policy judgement",
                 "Very hard: ambiguous, high stakes or needs deep expertise"],
}}


def difficulty(engine, masked_state):
    """0..1 from Jev's weighted score, or None when Jev cannot answer."""
    try:
        answer = engine.ask(masked_state, DIFFICULTY)["difficulty"]
    except JevUnavailable:
        return None
    return answer.value / (len(DIFFICULTY["difficulty"]["criteria"]) - 1)


def pick_tier(score):
    if score is None:
        return 1
    return 0 if score < THETA_LOW else 1 if score < THETA_HIGH else 2


class SpendCap:
    """Monthly paid-model spend. ponytail: in-process estimate; the Anthropic console limit is the hard stop
    and the cost counter (CR-ADP-04) will own the real figure."""

    def __init__(self, cap_usd=MONTHLY_CAP_USD, spent_usd=0.0):
        self.cap_usd, self.spent_usd = cap_usd, spent_usd

    def allows(self, usd):
        return self.spent_usd + usd <= self.cap_usd

    def record(self, usd):
        self.spent_usd += usd


@dataclass
class Reply:
    outcome: str                 # "answered", "escalate" (to a human) or "degraded" (no model answered)
    text: str | None = None
    tier: int | None = None
    provider: str | None = None
    cascaded: bool = False
    model: str | None = None


class Router:
    def __init__(self, tiers, cap=None, budget=None, sleep=None):
        """tiers: three lists of reliability.fallbacks.Provider, cheapest tier first."""
        self.tiers, self.cap, self.budget = tiers, cap or SpendCap(), budget or RetryBudget()
        self.sleep = sleep

    def chain(self, tier, messages, max_tokens):
        """The tier's providers, then every free provider from the other tiers, best first."""
        others = [p for t in sorted(range(len(self.tiers)), reverse=True) if t != tier for p in self.tiers[t]]
        chain = list(self.tiers[tier]) + [p for p in others if not p.paid]
        prompt_tokens = approx_tokens("".join(m["content"] for m in messages))
        return [p for p in chain if not p.paid or self.cap.allows(p.cost(prompt_tokens, max_tokens))]

    def generate(self, messages, tier, max_tokens, turn):
        options = {"sleep": self.sleep} if self.sleep else {}
        providers = {p.name: p for t in self.tiers for p in t}
        chain = self.chain(tier, messages, max_tokens)
        completion = call_chain(chain, messages, max_tokens, self.budget, turn, **options)
        used = providers[completion.provider]
        self.cap.record(used.cost(completion.input_tokens, completion.output_tokens))
        return completion

    def answer(self, messages, engine, masked_state, verify, max_tokens=4096):
        """verify(text) -> bool runs the output checks (schema, safety, promises, Jev confidence)."""
        tier, turn, outcome = pick_tier(difficulty(engine, masked_state)), {"retries": 0}, "escalate"
        tried = set()
        for step, current in enumerate((tier, tier + 1)):
            if current >= len(self.tiers):
                break
            chain = {p.name for p in self.chain(current, messages, max_tokens)}
            if outcome == "degraded" and not chain - tried:
                break  # one tier up offers nobody new (free tiers never fall back to Claude)
            tried |= chain
            try:
                completion = self.generate(messages, current, max_tokens, turn)
            except AllProvidersFailed as failed:
                log.warning("tier %s: no provider answered: %s", current, str(failed)[:400])
                outcome = "degraded"
                continue
            except ProviderError as refusal:  # only refusals escape the chain
                return Reply("escalate", tier=current, provider=refusal.provider)
            outcome = "escalate"
            if verify(completion.text):
                return Reply("answered", completion.text, current, completion.provider, cascaded=step == 1,
                             model=completion.model)
        return Reply(outcome, tier=current)
