"""Long-term evolution: the monthly user-distribution check, the quarterly owned-risk review, and the
retraining data pipeline that leaves out erased users (NFR-35 / CI-ADP-05: CI-D9, CI-D12). No model is trained
here: v1 has no GPU (a SPEC non-goal); the pipeline produces the corpus and keeps the checkpoint records.

- drift_report(): KL(P_observed || Q_eval) over intents for a rolling 30 days; at or above 0.15 the evaluation
  sampling weights are recalibrated to the observed shares (smoothed, so no intent drops to zero). Intents the
  evaluation set has never seen are reported: they need new episodes, not just a weight.
- The owned-risk review (quarterly): the open-issues register (the Genesis knowledge record that nothing
  supersedes) is the living risk log. Each item's frequency and severity this quarter against last quarter: a
  rise in either makes it an engineering epic; an item with no measurement is reported as unmeasured.
- The retraining corpus (quarterly): opted-in, tokenized evaluation samples of one region only, without any
  user who ever asked for erasure (any status in the erasure ledger: an erasure in progress counts). Its
  manifest records the content hash, the build time, the reasons examples were left out, and the member user
  hashes, so a later erasure can be traced to the checkpoints that hold it.
- Checkpoints (CI-D12): promoting a checkpoint that passed the full release gate retires every one of its
  region registered before it (their weights must be expunged; a newer candidate stays a candidate); a live
  checkpoint whose corpus holds a user erased later must be replaced by a retrain within 90 days of that
  erasure.
"""

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from safety.pii.tokens import find as find_pii

DRIFT_AT = 0.15
SMOOTHING = 1e-3
RETRAIN_WITHIN = timedelta(days=90)
REGISTER_TITLE = "Open issues register"


# The monthly distribution check -----------------------------------------------------------------------------

def shares(counts, keys, smoothing=SMOOTHING):
    total = sum(counts.get(k, 0) for k in keys) + smoothing * len(keys)
    return {k: (counts.get(k, 0) + smoothing) / total for k in keys}


def kl_divergence(p, q):
    return sum(p[k] * math.log(p[k] / q[k]) for k in p if p[k] > 0)


@dataclass(frozen=True)
class DriftReport:
    kl: float
    drifted: bool
    observed: dict
    assumed: dict
    weights: dict                  # the sampling weights to use: recalibrated when drifted, else the assumed
    unseen: tuple[str, ...]        # intents the evaluation set has no prior for


def drift_report(observed_counts, assumed_priors):
    """observed_counts: {intent: turns in the last 30 days}; assumed_priors: {intent: evaluation share}."""
    if not sum(observed_counts.values()):
        raise ValueError("no traffic to compare")
    keys = sorted(set(observed_counts) | set(assumed_priors))
    p = shares(observed_counts, keys)
    q = shares({k: v * 1_000_000 for k, v in assumed_priors.items()}, keys)
    kl = kl_divergence(p, q)
    drifted = kl >= DRIFT_AT
    unseen = tuple(sorted(k for k in observed_counts if observed_counts[k] and not assumed_priors.get(k)))
    return DriftReport(kl, drifted, p, q, p if drifted else q, unseen)


# The quarterly owned-risk review ----------------------------------------------------------------------------

ENTRY = re.compile(r"(?:^|[.:;]\s+|\n\s*)(?:NOT YET\s+)?([SBWL]\d{1,3})\s+(?=[A-Za-z('\"])")
KIND = {"S": "security", "B": "bug", "W": "wiring", "L": "accepted limit"}


def current_register(project_json):
    """The text of the open-issues register version that no other version supersedes."""
    state = json.loads(Path(project_json).read_text(encoding="utf-8"))
    versions = [k for k in state["knowledge"] if k["title"].startswith(REGISTER_TITLE)]
    superseded = {k.get("supersedes") for k in versions}
    current = [k for k in versions if k["id"] not in superseded]
    if len(current) != 1:
        raise ValueError(f"expected one current register, found {len(current)}")
    return current[0]["text"]


@dataclass(frozen=True)
class Risk:
    id: str
    kind: str
    text: str


def register_items(text):
    """The register's items in order: S (security), B (bugs), W (wiring), L (accepted limits)."""
    marks = [(m.start(1), m.group(1)) for m in ENTRY.finditer(text)]
    items, seen = [], set()
    for (start, item_id), (end, _) in zip(marks, [*marks[1:], (len(text), None)], strict=True):
        if item_id in seen:
            continue
        seen.add(item_id)
        body = re.sub(r"\s+", " ", text[start + len(item_id):end]).strip()
        body = re.split(r"\s(?:SECURITY|BUGS|WIRED|ACCEPTED LIMITS / DESIGN DEBT):", body)[0].strip()
        items.append(Risk(item_id, KIND[item_id[0]], body))
    return items


@dataclass(frozen=True)
class Signal:
    """One risk's measurements: occurrences (incidents, alerts, failures traced to it) and severity tier."""
    previous_count: int
    count: int
    previous_severity: int
    severity: int


@dataclass(frozen=True)
class RiskReview:
    epics: tuple[Risk, ...]          # rose in frequency or severity: on the roadmap
    steady: tuple[str, ...]
    unmeasured: tuple[str, ...]      # no signal this quarter: measure it before the next review


def owned_risk_review(items, signals):
    """signals: {risk id: Signal}; epics are ordered by severity, then by the rise in count."""
    rising = [(item, signals[item.id]) for item in items if item.id in signals
              and (signals[item.id].count > signals[item.id].previous_count
                   or signals[item.id].severity > signals[item.id].previous_severity)]
    rising.sort(key=lambda pair: (-pair[1].severity, -(pair[1].count - pair[1].previous_count), pair[0].id))
    epic_ids = {item.id for item, _ in rising}
    return RiskReview(epics=tuple(item for item, _ in rising),
                      steady=tuple(i.id for i in items if i.id in signals and i.id not in epic_ids),
                      unmeasured=tuple(i.id for i in items if i.id not in signals))


# The retraining corpus --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Example:
    tenant_id: str
    user_hash: str
    region: str
    turns: tuple[str, ...]
    created_at: datetime


def _context(conn, tenant_id, role):
    conn.execute(f"SET LOCAL ROLE {role}")
    conn.execute("SELECT set_config('app.current_tenant_id', %s, true)", (tenant_id,))


def erased_subjects(connect, tenant_ids):
    """Every user hash with an erasure request in the ledger, whatever its status: {hash: requested at}."""
    erased = {}
    for tenant_id in tenant_ids:
        with connect() as conn, conn.transaction():
            _context(conn, tenant_id, "lifecycle_service")
            for subject, at in conn.execute("SELECT subject_hash, created_at FROM erasure_ledger").fetchall():
                erased[subject] = min(at, erased.get(subject, at))
    return erased


def opted_in_samples(connect, tenant_ids):
    """The tenants' opted-in evaluation samples (evals.datasets eval_samples) as training examples."""
    out = []
    for tenant_id in tenant_ids:
        with connect() as conn, conn.transaction():
            _context(conn, tenant_id, "app_runtime")
            rows = conn.execute("SELECT region, user_hash, turns, created_at FROM eval_samples ORDER BY id")
            out += [Example(tenant_id, user, region, tuple(turns), at) for region, user, turns, at in rows]
    return out


@dataclass(frozen=True)
class Corpus:
    region: str
    examples: tuple[Example, ...]
    excluded: dict                          # reason -> count
    built_at: datetime
    members: tuple[str, ...] = ()           # user hashes in the corpus, sorted

    @property
    def content_hash(self):
        lines = (json.dumps({"user": e.user_hash, "turns": list(e.turns)}, ensure_ascii=False)
                 for e in self.examples)
        return hashlib.sha256("\n".join(lines).encode()).hexdigest()

    def manifest(self):
        return {"region": self.region, "content_hash": self.content_hash, "examples": len(self.examples),
                "excluded": dict(self.excluded), "built_at": self.built_at.isoformat(),
                "members": list(self.members)}


def build_corpus(examples, erased, region, now=None):
    """The examples one region may train on: tokenized, of that region, and of no user ever erased."""
    keep, excluded = [], Counter()
    for example in examples:
        if example.region != region:
            excluded["other_region"] += 1
        elif example.user_hash in erased:
            excluded["erased_user"] += 1
        elif any(find_pii(turn) for turn in example.turns):
            excluded["not_tokenized"] += 1
        else:
            keep.append(example)
    return Corpus(region, tuple(keep), dict(excluded), now or datetime.now(timezone.utc),
                  tuple(sorted({e.user_hash for e in keep})))


def write_corpus(corpus, directory):
    """corpus.jsonl and manifest.json under directory/<region>-<content hash>; returns that folder."""
    folder = Path(directory) / f"{corpus.region}-{corpus.content_hash[:16]}"
    folder.mkdir(parents=True, exist_ok=False)
    lines = (json.dumps({"turns": list(e.turns)}, ensure_ascii=False) + "\n" for e in corpus.examples)
    (folder / "corpus.jsonl").write_text("".join(lines), encoding="utf-8")
    (folder / "manifest.json").write_text(json.dumps(corpus.manifest(), indent=1), encoding="utf-8")
    return folder


# Checkpoints and their retirement ---------------------------------------------------------------------------

@dataclass
class Checkpoint:
    id: str
    region: str
    corpus: dict                   # the corpus manifest it was trained on
    status: str = "candidate"      # candidate, live or retired
    retired_at: str | None = None
    history: list = field(default_factory=list)


class Checkpoints:
    """The record of fine-tuned checkpoints per region, in a JSON file."""

    def __init__(self, path):
        self.path = Path(path)

    def all(self):
        if not self.path.exists():
            return []
        return [Checkpoint(**c) for c in json.loads(self.path.read_text(encoding="utf-8"))]

    def _save(self, checkpoints):
        self.path.write_text(json.dumps([asdict(c) for c in checkpoints], indent=1), encoding="utf-8")

    def register(self, checkpoint_id, corpus_manifest):
        checkpoints = self.all()
        if any(c.id == checkpoint_id for c in checkpoints):
            raise ValueError(f"{checkpoint_id} is already registered")
        checkpoints.append(Checkpoint(checkpoint_id, corpus_manifest["region"], corpus_manifest))
        self._save(checkpoints)

    def promote(self, checkpoint_id, gate_passed, now=None):
        """Make a candidate live after the full release gate; returns the checkpoints to expunge."""
        if gate_passed is not True:
            raise ValueError("a checkpoint goes live only after the full release gate passes")
        now = (now or datetime.now(timezone.utc)).isoformat()
        checkpoints = self.all()
        target = next((c for c in checkpoints if c.id == checkpoint_id), None)
        if target is None or target.status != "candidate":
            raise ValueError(f"{checkpoint_id} is not a candidate")
        retired = []
        for c in checkpoints[:checkpoints.index(target)]:       # earlier ones: a newer candidate stays
            if c.region == target.region and c.status != "retired":
                c.status, c.retired_at = "retired", now
                c.history.append({"at": now, "event": f"retired by {target.id}"})
                retired.append(c.id)
        target.status = "live"
        target.history.append({"at": now, "event": "promoted"})
        self._save(checkpoints)
        return retired

    def erasure_deadlines(self, erased, now=None):
        """{checkpoint id: (deadline, overdue)} for non-retired checkpoints whose corpus holds an erased
        user."""
        now = now or datetime.now(timezone.utc)
        out = {}
        for c in self.all():
            if c.status == "retired":
                continue
            hits = [erased[u] for u in c.corpus.get("members", []) if u in erased]
            if hits:
                deadline = min(hits) + RETRAIN_WITHIN
                out[c.id] = (deadline, now > deadline)
        return out
