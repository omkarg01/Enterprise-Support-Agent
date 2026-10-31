"""Feedback signals, Jev triage of free text, and the weekly failure review (NFR-31 / CI-ADP-01: CI-D1,
CI-D11; NFR-32 / CI-ADP-02: CI-D2, CI-D3).

- Signals (CI-D1), one append-only row each: explicit (a thumbs up or down on a reply; the post-resolution
  survey, a 1-5 customer effort score where 1 is effortless, with an optional comment), operational (a
  specialist's reason code on a co-pilot draft or a rejected action) and implicit (a re-asked question, a case
  reopened within 7 days, a conversation abandoned after an unresolved reply). Rows name the user by subject
  hash only, carry tokenized text only (refused if anything personal is left), and are erased per user.
- Triage (CI-D11): a comment is classified by one Jev request: a choice over the taxonomy (taxonomy.py) and
  a five-level severity score, s in [0, 1]. s >= 0.80 pages on-call at once. A category Jev is unsure of
  (P < 0.70), and anything Jev cannot answer, is UNCLASSIFIED, kept for the monthly taxonomy review; an
  unknown severity is never read as a mild one: the item goes to the weekly review as needing a person.
- Weekly review (CI-D3): clusters by taxonomy node, ranked by priority = volume x the node's severity weight
  (100 / 10 / 1); the top three get the owner of their subsystem; every event in a postmortem node needs a
  blameless postmortem, except one a specialist stopped before it ran (a rejection is the gate working).
- live_metrics(): CSAT (share of thumbs up), customer effort (mean score) and first-contact resolution (one
  minus the reopen rate), the live numbers the release gate compares (evals.gate).
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta

from core.orchestrator.decisions import JevUnavailable, Masked
from data.migrate import psql
from improvement.feedback import taxonomy
from observability.tracing import subject_hash
from safety.pii.tokens import find as find_pii

PAGE_AT = 0.80
CATEGORY_AT = 0.70
REASK_AT = 0.85
REOPEN_WINDOW = timedelta(days=7)
TOP = 3
SEVERITY = {"type": "score",
            "instructions": "How severe, dangerous or high-risk is the failure this feedback describes?",
            "criteria": ["Cosmetic: wording, formatting or a minor annoyance",
                         "Inconvenient: extra effort or waiting, the issue was still solved",
                         "Unresolved: the customer's problem was not solved",
                         "Harmful: wrong information or a wrong action that affects the customer",
                         "Critical: money, personal data, security or access went wrong"]}
CATEGORY = {"type": "choice", "instructions": "Which failure does this customer feedback describe?",
            "criteria": {node.id: node.description for node in taxonomy.TREE.values()}}
KINDS = {"thumbs": {"up", "down"}, "survey": {"ces"}, "reason_code": set(), "implicit": {"reask", "reopen",
                                                                                          "abandon"}}

SCHEMA_NAME = "improvement_0001_feedback.sql"
SCHEMA = """
CREATE TABLE feedback_events (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id       text NOT NULL REFERENCES tenants (id),
  user_hash       text NOT NULL,
  conversation_id text NOT NULL,
  turn_id         text,
  kind            text NOT NULL CHECK (kind IN ('thumbs', 'survey', 'reason_code', 'implicit')),
  signal          text NOT NULL,
  ces             smallint CHECK (ces BETWEEN 1 AND 5),
  comment         text,
  category        text,
  severity        numeric(4, 3) CHECK (severity BETWEEN 0 AND 1),
  source          text,
  taxonomy        text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX feedback_events_week ON feedback_events (tenant_id, created_at);
CREATE INDEX feedback_events_user ON feedback_events (tenant_id, user_hash);
ALTER TABLE feedback_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE feedback_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON feedback_events
  USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());
ALTER TABLE feedback_events OWNER TO app_owner;
GRANT SELECT, INSERT, DELETE ON feedback_events TO app_runtime;
"""


def apply_schema(database):
    if not psql(database, f"SELECT 1 FROM schema_migrations WHERE name = '{SCHEMA_NAME}'"):
        psql(database, f"BEGIN;\n{SCHEMA}\nINSERT INTO schema_migrations (name) VALUES ('{SCHEMA_NAME}');\n"
                       "COMMIT;\n")


def _context(conn, tenant_id):
    conn.execute("SET LOCAL ROLE app_runtime")
    conn.execute("SELECT set_config('app.current_tenant_id', %s, true)", (tenant_id,))


# Triage -----------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Triage:
    category: str                    # a taxonomy node id, or taxonomy.UNCLASSIFIED
    severity: float | None           # 0..1; None when Jev could not say
    source: str                      # "jev" or "residual"
    confidence: float | None = None

    @property
    def page(self):
        return self.severity is not None and self.severity >= PAGE_AT


def triage(engine, comment):
    """Classify one tokenized comment (Masked) in one Jev request."""
    try:
        answers = engine.ask({"feedback": comment}, {"category": CATEGORY, "severity": SEVERITY})
    except JevUnavailable:
        return Triage(taxonomy.UNCLASSIFIED, None, "residual")
    category, severity = answers["category"], answers["severity"]
    level = severity.index / (len(SEVERITY["criteria"]) - 1)
    if category.confidence < CATEGORY_AT:
        return Triage(taxonomy.UNCLASSIFIED, level, "residual", category.confidence)
    return Triage(category.value, level, "jev", category.confidence)


# Implicit signals -------------------------------------------------------------------------------------------

def _grams(text):
    words = re.findall(r"[a-z0-9']+", text.lower())
    return Counter(words) + Counter(zip(words, words[1:], strict=False))


def similarity(a, b):
    """Cosine over word and word-pair counts: a lexical stand-in until an embedding model is wired."""
    left, right = _grams(a), _grams(b)
    dot = sum(left[g] * right[g] for g in left.keys() & right.keys())
    norm = (sum(v * v for v in left.values()) * sum(v * v for v in right.values())) ** 0.5
    return dot / norm if norm else 0.0


def reasked(previous, current):
    return similarity(previous, current) >= REASK_AT


def reopened(resolved_at, opened_at):
    """A new conversation on the same case within 7 days of its resolution: a first-contact failure."""
    return resolved_at is not None and timedelta(0) <= opened_at - resolved_at <= REOPEN_WINDOW


def abandoned(last_reply_resolved, timed_out):
    return timed_out and not last_reply_resolved


# The store --------------------------------------------------------------------------------------------------

class Feedback:
    def __init__(self, connect, tokenizer, engine, page):
        """page(tenant_id, event): on-call's pager (injected), called for every severity >= 0.80."""
        self.connect, self.tokenizer, self.engine, self.page = connect, tokenizer, engine, page

    def _masked(self, text):
        masked = self.tokenizer.mask(text)[0]
        if find_pii(masked):
            raise ValueError("the comment still holds personal data after tokenization")
        return masked

    def _insert(self, tenant_id, principal_id, conversation_id, kind, signal, turn_id=None, ces=None,
                comment=None, triaged=None):
        if kind not in KINDS or (KINDS[kind] and signal not in KINDS[kind]):
            raise ValueError(f"unknown signal {kind}/{signal}")
        result = (triaged.category, triaged.severity, triaged.source) if triaged else (None, None, None)
        row = (tenant_id, subject_hash(tenant_id, principal_id), conversation_id, turn_id, kind, signal, ces,
               comment, *result, taxonomy.VERSION)
        with self.connect() as conn, conn.transaction():
            _context(conn, tenant_id)
            (event_id,) = conn.execute(
                "INSERT INTO feedback_events (tenant_id, user_hash, conversation_id, turn_id, kind, signal, "
                "ces, comment, category, severity, source, taxonomy) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, "
                "%s, %s, %s, %s) RETURNING id", row).fetchone()
        return event_id

    def thumbs(self, tenant_id, principal_id, conversation_id, turn_id, up):
        signal = "up" if up else "down"
        return self._insert(tenant_id, principal_id, conversation_id, "thumbs", signal, turn_id)

    def survey(self, tenant_id, principal_id, conversation_id, ces, comment=None):
        """The post-resolution card: effort 1 (effortless) to 5, and optional free text, triaged by Jev."""
        if type(ces) is not int or not 1 <= ces <= 5:
            raise ValueError("the effort score is a whole number from 1 to 5")
        masked = self._masked(comment) if comment and comment.strip() else None
        triaged = triage(self.engine, Masked(masked)) if masked else None
        event_id = self._insert(tenant_id, principal_id, conversation_id, "survey", "ces", ces=ces,
                                comment=masked, triaged=triaged)
        if triaged and triaged.page:
            self.page(tenant_id, {"event_id": event_id, "category": triaged.category,
                                  "severity": triaged.severity, "comment": masked})
        return event_id, triaged

    def reason_code(self, tenant_id, principal_id, conversation_id, code, rejection=False):
        """A specialist's reason code (HL-D10): source "rejection" for an action stopped before it ran."""
        node = taxonomy.node(code)
        triaged = Triage(node.id, None, "rejection" if rejection else "reason_code")
        return self._insert(tenant_id, principal_id, conversation_id, "reason_code", code, triaged=triaged)

    def implicit(self, tenant_id, principal_id, conversation_id, signal):
        return self._insert(tenant_id, principal_id, conversation_id, "implicit", signal)

    def events(self, tenant_id, since, until):
        with self.connect() as conn, conn.transaction():
            _context(conn, tenant_id)
            rows = conn.execute(
                "SELECT id, kind, signal, ces, category, severity, source, created_at FROM feedback_events "
                "WHERE created_at >= %s AND created_at < %s ORDER BY id", (since, until)).fetchall()
        return [Event(*row) for row in rows]

    def erase(self, tenant_id, principal_id):
        with self.connect() as conn, conn.transaction():
            _context(conn, tenant_id)
            return conn.execute("DELETE FROM feedback_events WHERE user_hash = %s",
                                (subject_hash(tenant_id, principal_id),)).rowcount


@dataclass(frozen=True)
class Event:
    id: int
    kind: str
    signal: str
    ces: int | None = None
    category: str | None = None
    severity: float | None = None
    source: str | None = None
    created_at: object = None


# The weekly review ------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Cluster:
    category: str
    volume: int
    weight: int
    owner: str | None = None

    @property
    def priority(self):
        return self.volume * self.weight


@dataclass(frozen=True)
class Review:
    clusters: tuple[Cluster, ...]                  # ranked by priority
    postmortems: tuple[int, ...]                   # event ids needing a blameless postmortem
    high_severity: tuple[int, ...]                 # s >= 0.80
    needs_person: tuple[int, ...] = ()             # comments whose severity is unknown
    unclassified: int = 0                          # for the monthly taxonomy review
    owners_missing: tuple[str, ...] = field(default=())


def weekly_review(events, owners):
    """events: the week's Events; owners: {subsystem: engineer} for the top three clusters."""
    classified = [e for e in events if e.category]
    counts = Counter(e.category for e in classified)
    ranked = sorted((Cluster(c, n, taxonomy.TREE[c].weight) for c, n in counts.items()),
                    key=lambda c: (-c.priority, c.category))
    top = [Cluster(c.category, c.volume, c.weight, owners.get(taxonomy.TREE[c.category].subsystem))
           for c in ranked[:TOP]]
    return Review(
        clusters=tuple(top + ranked[TOP:]),
        postmortems=tuple(e.id for e in classified
                          if taxonomy.TREE[e.category].postmortem and e.source != "rejection"),
        high_severity=tuple(e.id for e in classified if e.severity is not None and e.severity >= PAGE_AT),
        needs_person=tuple(e.id for e in classified if e.kind == "survey" and e.severity is None),
        unclassified=counts.get(taxonomy.UNCLASSIFIED, 0),
        owners_missing=tuple(sorted({taxonomy.TREE[c.category].subsystem for c in top if c.owner is None})))


def live_metrics(events, conversations, resolved):
    """{"csat", "ces", "fcr", "reask_rate", "abandonment_rate", "reopen_rate"} over a period; conversations
    and resolved are counts for the same period. A number without data is None."""
    thumbs = [e for e in events if e.kind == "thumbs"]
    efforts = [e.ces for e in events if e.kind == "survey"]
    implicit = Counter(e.signal for e in events if e.kind == "implicit")

    def rate(count, total):
        return count / total if total else None
    reopen = rate(implicit["reopen"], resolved)
    return {"csat": rate(sum(e.signal == "up" for e in thumbs), len(thumbs)),
            "ces": sum(efforts) / len(efforts) if efforts else None,
            "fcr": None if reopen is None else 1 - reopen,
            "reask_rate": rate(implicit["reask"], conversations),
            "abandonment_rate": rate(implicit["abandon"], conversations),
            "reopen_rate": reopen}
