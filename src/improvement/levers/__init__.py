"""Improvement levers, the few-shot quarantine, and the rules a fix must pass (NFR-33 / CI-ADP-03: CI-D4,
CI-D5; NFR-34 / CI-ADP-04: CI-D7, CI-D8, CI-D10). Knowledge articles (CI-D6) are in articles.py.

- Levers (CI-D4): prompt, Jev question, threshold, tool description, knowledge article, curated few-shot
  example, per-region fine-tuning. suggested_levers() gives the usual ones for a taxonomy node; fine-tuning is
  not run in v1 (no GPU, a SPEC non-goal), so a fix that pulls it is refused.
- Few-shot quarantine (CI-D5): curated examples live in an append-only registry (few_shot.jsonl) where each
  entry is tokenized text with the SHA-256 of every normalised utterance, and each entry's hash covers the one
  before it, so an edit or a removal breaks the chain. curate() refuses an example whose utterances already
  appear in an evaluation set; contamination() is the CI check the other way: no registered utterance may
  appear in any evaluation set (tests/improvement/levers runs it over the repository's).
- A fix (CI-D7, CI-D8, CI-D10) names its failure cluster, its lever, and the regression tests written for the
  cluster first; ready_to_merge() lists what it still lacks: those tests (they must exist), the full release
  gate, a significant before / after drop of the cluster's error rate on its own episodes (one-sided two-
  proportion test, p < 0.05), a read-only canary no worse than baseline (re-asks, CSAT, latency, no burn
  alert; a fix touching a write tool cannot use canary evidence), and a reviewer who is not the author.
"""

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from improvement.feedback import taxonomy
from safety.pii.tokens import find as find_pii

REGISTRY = Path(__file__).with_name("few_shot.jsonl")
SIGNIFICANCE = 0.05
GENESIS = "0" * 64


class Lever(str, Enum):
    PROMPT = "prompt"
    JEV_QUESTION = "jev_question"
    THRESHOLD = "threshold"
    TOOL_DESCRIPTION = "tool_description"
    KB_ARTICLE = "kb_article"
    FEW_SHOT = "few_shot"
    FINE_TUNE = "fine_tune"


BY_SUBSYSTEM = {
    "triage": (Lever.JEV_QUESTION, Lever.THRESHOLD),
    "retrieval": (Lever.KB_ARTICLE, Lever.PROMPT),
    "multi_agent": (Lever.JEV_QUESTION, Lever.PROMPT),
    "tools": (Lever.TOOL_DESCRIPTION, Lever.FEW_SHOT),
    "safety": (Lever.PROMPT, Lever.THRESHOLD),
    "gate": (Lever.THRESHOLD, Lever.PROMPT),
    "infrastructure": (),                                   # an engineering fix, not a behaviour lever
    "experience": (Lever.PROMPT, Lever.FEW_SHOT),
    taxonomy.UNCLASSIFIED: (),
}


def suggested_levers(category):
    return BY_SUBSYSTEM[taxonomy.TREE[category].subsystem]


# The few-shot quarantine ------------------------------------------------------------------------------------

def normalise(text):
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def fingerprint(text):
    return hashlib.sha256(normalise(text).encode()).hexdigest()


def corpus_hashes(texts):
    return {fingerprint(text) for text in texts if normalise(text)}


@dataclass(frozen=True)
class Exemplar:
    id: str
    lever_for: str                 # the taxonomy node it teaches
    turns: tuple[str, ...]         # tokenized
    hashes: tuple[str, ...]
    curated_by: str
    previous: str


def _entry_hash(lever_for, turns, curated_by, previous):
    body = json.dumps([lever_for, list(turns), curated_by, previous], ensure_ascii=False)
    return hashlib.sha256(body.encode()).hexdigest()


class Contaminated(ValueError):
    pass


class FewShotRegistry:
    def __init__(self, path=REGISTRY):
        self.path = Path(path)

    def entries(self):
        if not self.path.exists():
            return []
        return [Exemplar(e["id"], e["lever_for"], tuple(e["turns"]), tuple(e["hashes"]), e["curated_by"],
                         e["previous"])
                for e in (json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()
                          if line.strip())]

    def verify(self):
        """Problems with the chain: an entry edited, removed or reordered, or text that is not tokenized."""
        problems, previous = [], GENESIS
        for entry in self.entries():
            if entry.previous != previous:
                problems.append(f"{entry.id[:12]}: chain broken (an entry before it was edited or removed)")
            if entry.id != _entry_hash(entry.lever_for, entry.turns, entry.curated_by, entry.previous):
                problems.append(f"{entry.id[:12]}: its content was edited")
            if tuple(fingerprint(t) for t in entry.turns) != entry.hashes:
                problems.append(f"{entry.id[:12]}: hashes do not match its turns")
            if any(find_pii(turn) for turn in entry.turns):
                problems.append(f"{entry.id[:12]}: holds personal data")
            previous = entry.id
        return problems

    def curate(self, lever_for, turns, curated_by, evaluation_hashes):
        """Append one tokenized example; refused if any utterance is in an evaluation set already."""
        taxonomy.node(lever_for)
        turns = tuple(turns)
        if not turns or any(find_pii(turn) for turn in turns):
            raise ValueError("an example needs tokenized turns with no personal data left")
        hashes = tuple(fingerprint(turn) for turn in turns)
        clashes = sorted(set(hashes) & set(evaluation_hashes))
        if clashes:
            raise Contaminated(f"{len(clashes)} utterances already appear in an evaluation set")
        entries = self.entries()
        previous = entries[-1].id if entries else GENESIS
        entry_id = _entry_hash(lever_for, turns, curated_by, previous)
        with self.path.open("a", encoding="utf-8") as registry:
            entry = {"id": entry_id, "lever_for": lever_for, "turns": list(turns), "hashes": list(hashes),
                     "curated_by": curated_by, "previous": previous}
            registry.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry_id

    def hashes(self):
        return {h for entry in self.entries() for h in entry.hashes}


def contamination(registry, evaluation_texts):
    """Registered utterances that also appear in the evaluation texts (must be empty)."""
    return sorted(registry.hashes() & corpus_hashes(evaluation_texts))


# A fix and what it needs to merge ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ClusterRun:
    """The motivating cluster's own episodes, run on one version: how many, and how many failed."""
    episodes: int
    failures: int

    @property
    def rate(self):
        return self.failures / self.episodes if self.episodes else None


@dataclass(frozen=True)
class Canary:
    route: str
    baseline: dict                 # {"reask_rate", "csat", "p95_ms"}
    candidate: dict
    burn_alerts: int = 0


@dataclass(frozen=True)
class Fix:
    cluster: str                   # a taxonomy node
    lever: Lever
    author: str
    tests: tuple[str, ...]         # "path::test_name" or "episode:<id>", written for the cluster first
    touches_write_tool: bool = False
    gate_passed: bool | None = None
    gate_failures: tuple[str, ...] = ()
    before: ClusterRun | None = None
    after: ClusterRun | None = None
    canary: Canary | None = None
    reviewer: str | None = None
    notes: dict = field(default_factory=dict)


def drop_p_value(before, after):
    """One-sided two-proportion z test that the failure rate fell from before to after."""
    if not before.episodes or not after.episodes:
        return 1.0
    pooled = (before.failures + after.failures) / (before.episodes + after.episodes)
    spread = math.sqrt(pooled * (1 - pooled) * (1 / before.episodes + 1 / after.episodes))
    if spread == 0:
        return 0.0 if after.rate < before.rate else 1.0
    z = (before.rate - after.rate) / spread
    return 0.5 * math.erfc(z / math.sqrt(2))


def ready_to_merge(fix, test_exists, read_only_routes):
    """What the fix still lacks; [] when it may merge. test_exists(ref) -> bool."""
    problems = []
    taxonomy.node(fix.cluster)
    if fix.lever is Lever.FINE_TUNE:
        problems.append("per-region fine-tuning is not run in v1 (no GPU, a non-goal)")
    if not fix.tests:
        problems.append("the failure cluster has no regression test: write it before the fix (CI-D7)")
    problems += [f"regression test {ref} does not exist" for ref in fix.tests if not test_exists(ref)]
    if fix.gate_passed is not True:
        problems.append("the full release gate has not passed"
                        + (f": {'; '.join(fix.gate_failures)}" if fix.gate_failures else ""))
    if fix.before is None or fix.after is None:
        problems.append("no before / after run on the cluster's episodes")
    elif not fix.after.rate < fix.before.rate or drop_p_value(fix.before, fix.after) >= SIGNIFICANCE:
        problems.append(f"the cluster's failure rate did not drop significantly ({fix.before.rate:.2%} -> "
                        f"{fix.after.rate:.2%}, p = {drop_p_value(fix.before, fix.after):.3f})")
    canary = fix.canary
    if canary is None:
        problems.append("no read-only canary result")
    elif fix.touches_write_tool:
        problems.append("a fix that touches a write tool cannot be proven on a canary; it needs risk-tier "
                        "pass^k and shadow evidence")
    elif canary.route not in read_only_routes:
        problems.append(f"canary route {canary.route!r} is not read-only")
    else:
        base, cand = canary.baseline, canary.candidate
        if cand["reask_rate"] > base["reask_rate"] or cand["csat"] < base["csat"]:
            problems.append("the canary's re-ask rate rose or its CSAT fell")
        if cand["p95_ms"] > base["p95_ms"] * 1.10:
            problems.append("the canary's p95 latency rose by more than 10%")
        if canary.burn_alerts:
            problems.append(f"{canary.burn_alerts} SLO burn-rate alerts during the canary")
    if not fix.reviewer:
        problems.append("no code review")
    elif fix.reviewer == fix.author:
        problems.append("the author cannot review their own fix")
    return problems


def regression_test_lookup(root):
    """test_exists for ready_to_merge: "path::name" is a test function in that file under root, and an
    evaluation episode id ("episode:<id>") is in the framework seeds."""
    root = Path(root)

    def exists(ref):
        if ref.startswith("episode:"):
            seeds_file = root / "evals" / "datasets" / "framework_seeds.json"
            seeds = json.loads(seeds_file.read_text(encoding="utf-8"))
            return ref.removeprefix("episode:") in {episode["id"] for episode in seeds}
        path, _, name = ref.partition("::")
        source = root / path
        return bool(name) and source.is_file() and bool(
            re.search(rf"^(?:async )?def {re.escape(name)}\(", source.read_text(encoding="utf-8"), re.M))
    return exists
