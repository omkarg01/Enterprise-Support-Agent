"""Merging specialist findings into one reply in one voice (FR-29 / MA-ADP-05: MA-D7, MA-D16, MA-D12, MA-D13,
MA-D15, MA-D17).

merge(engine, results, evidence, draft) -> Outcome, exactly one per turn:

1. claims: each specialist result's typed claims (claims_from: until agents.topology carries the contracts,
   a completed result's findings are a JSON list of claims). A claim is a topic, an optional number with its
   unit, its text and the evidence ids it cites; the role is the result's, never the claim's own say.
   Newest first (newest()): on one topic, claims citing an older article than another claim are dropped,
   before any conflict check, so a superseded article never makes a discrepancy. Then, on a topic whose
   claims differ but all cite one same customer record, only the lead's stand (lead_keeps()).
2. numbers, in code only: two claims on the same topic with numbers that differ (beyond the unit's
   tolerance), or in different units, go to a person with the values side by side. Jev never judges numbers.
3. other disagreements: Jev scores, on masked text, how strongly each claim's own cited evidence supports it
   (five levels). One claim at four or five while every other is at one or two wins; anything closer goes
   to a person. Jev unavailable -> a person.
4. the reply: draft(accepted claims) is called once, by the coordinator (the only author of customer text).
   Jev says what the reply asks the customer to do (REDIRECT, a choice): act themselves, including through
   their own administrators, roles or forms (self_service), or hand the problem to someone at the support
   provider (hand_off), or both. hand_off or both at HOLD_AT or more holds it: redraft once (redraft), then a
   person (register B25: the old yes / no question also held self-service answers that name the customer's
   own Tenant Owner or forms). Jev unavailable, or a claimed completed action no verified write backs, also
   go to a person.
"""

import json
import re
import string
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from core.orchestrator.decisions import JevUnavailable, Masked

HOLD, HOLD_AT = ("hand_off", "both"), 0.5   # set on new example replies (KNOWLEDGE-a7bd69df), golden held out
REJECTED_EXCERPT = 400                        # characters of a held draft kept in the internal trace
WIN_AT, LOSE_AT = 4, 2            # support levels, one to five
TOLERANCE = {"percent": Decimal("0.005")}  # per unit; exact otherwise
SUPPORT = {"type": "score",
           "instructions": "How strongly does the cited evidence support this factual claim?",
           "criteria": ["Unsupported", "Weak", "Plausible", "Strong", "Definitive"]}
REDIRECT = {"type": "choice", "instructions": "What does this reply ask the customer to do?", "criteria": {
    "self_service": ("Act on steps or facts themselves, including through their own administrators, roles, "
                     "settings or self-service forms"),
    "hand_off": ("Contact, or wait for, another team, department, person or specialist at the support "
                 "provider to resolve it"),
    "both": ("Act on some steps themselves and also contact another team or specialist at the support "
             "provider")}}
COMPLETED = re.compile(r"\b(?:have|has|had)\s+(?:been\s+)?(?:processed|completed|issued|refunded|credited|"
                       r"applied|reset|cancelled|canceled|deleted|executed|sent)\b|\b(?:is|are)\s+now\s+"
                       r"(?:refunded|credited|reset|cancelled|canceled|deleted)\b", re.IGNORECASE)


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    topic: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=2000)
    value: Decimal | None = None
    unit: str | None = None
    evidence: tuple[str, ...] = Field(min_length=1)
    role: str = ""

    @field_validator("value")
    @classmethod
    def _finite(cls, value):
        if value is not None and not value.is_finite():
            raise ValueError("a claimed number must be finite")
        return value


@dataclass(frozen=True)
class Outcome:
    kind: str                       # "reply" or "human"
    text: str | None = None         # the one reply, when kind is "reply"
    reason: str | None = None       # why a person takes it: a code
    detail: dict = field(default_factory=dict)
    accepted: tuple[Claim, ...] = ()


def claims_from(result):
    """The typed claims of a completed specialist result, labelled with the result's role; [] when the result
    is not completed or its claims are malformed (they are then not used)."""
    if result.get("status") != "completed":
        return []
    try:
        raw = json.loads(result["findings"])
        return [Claim(**{**item, "role": result["role"]}) for item in raw]
    except (TypeError, ValueError, KeyError, ValidationError, InvalidOperation):
        return []


def masked(text):
    """Labelled for Jev; DecisionEngine still refuses text that holds personal data (SecurityViolation)."""
    return Masked(text)


def human(reason, **detail):
    return Outcome("human", reason=reason, detail=detail)


def numeric_conflicts(claims):
    by_topic = {}
    for claim in claims:
        if claim.value is not None:
            by_topic.setdefault(claim.topic, []).append(claim)
    conflicts = {}
    for topic, group in by_topic.items():
        units = {c.unit for c in group}
        values = [c.value for c in group]
        tolerance = TOLERANCE.get(next(iter(units)) or "", Decimal(0)) if len(units) == 1 else None
        if tolerance is None or max(values) - min(values) > tolerance:
            conflicts[topic] = {c.role: f"{c.value} {c.unit or ''}".strip() for c in group}
    return conflicts


def adjudicate(engine, topic, group, evidence):
    """The winning claim of a non-numeric disagreement, or None (a person decides)."""
    state, questions, names = {}, {}, string.ascii_lowercase[:len(group)]
    for name, claim in zip(names, group, strict=True):  # letters: Jev questions carry no digits
        state[f"claim_{name}"] = masked(claim.text)
        state[f"evidence_{name}"] = masked("\n\n".join(evidence.get(e, "(missing evidence)")
                                                       for e in claim.evidence))
        questions[f"support_{name}"] = {**SUPPORT, "instructions": f"{SUPPORT['instructions']} Judge "
                                        f"claim_{name} against evidence_{name} only."}
    answers = engine.ask(state, questions)
    levels = [answers[f"support_{name}"].index + 1 for name in names]
    winners = [n for n, level in enumerate(levels) if level >= WIN_AT]
    if winners and all(level <= LOSE_AT for n, level in enumerate(levels) if n != winners[0]):
        return group[winners[0]], levels
    return None, levels


def redirect_of(engine, text):
    """(what the reply asks the customer to do, Jev's confidence in it, held?)."""
    answer = engine.ask({"draft": masked(text)}, {"redirect": REDIRECT})["redirect"]
    return answer.value, round(answer.confidence, 2), answer.value in HOLD and answer.confidence >= HOLD_AT


def newest(claims, dated):
    """Per topic, an older article never outvotes a newer one (the user's rule, 2026-11-20): of the claims that
    cite dated evidence (an article's updated_at), only those citing the latest date stay. Claims citing no
    dated evidence (tool records) are kept as they are."""
    def date(claim):
        return max((dated[e] for e in claim.evidence if dated.get(e)), default=None)
    latest = {}
    for claim in claims:
        if date(claim):
            latest[claim.topic] = max(latest.get(claim.topic, ""), date(claim))
    return [claim for claim in claims if date(claim) is None or date(claim) == latest[claim.topic]]


def lead_keeps(claims, lead, records):
    """M1: on a topic whose claims differ but all cite one same customer record, the lead's claims stand: two
    readings of one record are not a dispute. Returns (claims, the topics settled that way)."""
    by_topic = {}
    for claim in claims:
        by_topic.setdefault(claim.topic, []).append(claim)
    kept, settled = [], []
    for topic, group in by_topic.items():
        differ = len({(c.text, c.value, c.unit) for c in group}) > 1
        shared = set.intersection(*(set(c.evidence) for c in group)) & set(records)
        mine = [c for c in group if c.role == lead]
        if differ and shared and mine:
            kept += mine
            settled.append(topic)
        else:
            kept += group
    return kept, settled


def merge(engine, results, evidence, draft, verified_actions=(), redraft=None, dated=None, lead=None,
          records=()):
    """evidence: {evidence id: masked text}; draft(claims) -> the coordinator's reply text; verified_actions:
    executed writes their read-back confirmed (agents.contracts.ExecutedAction); redraft(claims): one more
    draft when the first points the customer elsewhere (register B25), checked again before it is used;
    dated: {evidence id: its article's updated_at}, so the newest article wins a topic (newest());
    lead, records: the lead's role and the evidence ids that are customer records (lead_keeps())."""
    claims = newest([claim for result in results for claim in claims_from(result)], dated or {})
    claims, settled = lead_keeps(claims, lead, records)
    outcome = _decide(engine, claims, evidence, draft, verified_actions, redraft)
    return replace(outcome, detail={**outcome.detail, "lead_kept": settled}) if settled else outcome


def _decide(engine, claims, evidence, draft, verified_actions, redraft):
    """merge() once the claims are in: the conflict checks, the one draft and its redirect check."""
    if not claims:
        return human("NOTHING_TO_SAY")
    numbers = numeric_conflicts(claims)
    if numbers:
        return human("NUMERIC_DISCREPANCY", values=numbers)
    accepted, by_topic = [], {}
    for claim in claims:
        by_topic.setdefault(claim.topic, []).append(claim)
    try:
        for topic, group in by_topic.items():
            distinct = list({c.text: c for c in group}.values())
            if len(distinct) == 1 or all(c.value is not None for c in distinct):
                accepted.append(distinct[0])
                continue
            winner, levels = adjudicate(engine, topic, distinct, evidence)
            if winner is None:
                return human("EVIDENCE_AMBIGUITY", topic=topic,
                             claims={c.role: (c.text, level)
                                     for c, level in zip(distinct, levels, strict=True)})
            accepted.append(winner)
        text = draft(tuple(accepted))
        choice, confidence, held = redirect_of(engine, text)
        checked = {"redirect": choice, "redirect_confidence": confidence, "redrafted": False}
        if held and redraft is not None:
            checked.update(redrafted=True, rejected_draft=text[:REJECTED_EXCERPT])
            text = redraft(tuple(accepted))
            choice, confidence, held = redirect_of(engine, text)
            checked.update(redirect=choice, redirect_confidence=confidence)
    except JevUnavailable:
        return human("JEV_UNAVAILABLE")
    if held:
        return human("REDIRECT", draft=text, **checked)
    if COMPLETED.search(text) and not verified_actions:
        return human("UNVERIFIED_COMPLETION", draft=text, **checked)
    return Outcome("reply", text=text, detail=checked, accepted=tuple(accepted))
