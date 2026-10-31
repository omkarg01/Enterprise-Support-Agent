"""Knowledge articles the agent drafts and a person publishes (NFR-33 / CI-ADP-03: CI-D6; KR-D13, KR-D16).

- gap_topics(): the weekly review's knowledge-gap clusters with enough volume to deserve an article.
- Articles.draft(): from the masked texts of successfully resolved cases on the topic, a draft written by
  generate(prompt) (the drafting model, injected) and stored tokenized, audience internal, author agent. It
  cannot be retrieved by customers: knowledge.ingest only shows customer_visible documents to them.
- publish(): only an active staff member with the knowledge_manager role, verified by verify_staff (the staff
  sign-in, hitl.approval.staff_verifier), and never the agent, may publish; it returns the knowledge.ingest
  Document to index, customer_visible, provenance agent. reject() records why a draft was not published. A
  draft's text never changes after it is written: a correction is a new draft.
"""

import uuid

from data.migrate import psql
from improvement.feedback import taxonomy
from knowledge.ingest.pipeline import Document
from safety.pii.tokens import find as find_pii

MIN_GAP_VOLUME = 3
PUBLISHER_ROLE = "knowledge_manager"
AGENT = "agent"

SCHEMA_NAME = "improvement_0002_articles.sql"
SCHEMA = """
CREATE TABLE kb_article_drafts (
  id           text PRIMARY KEY,
  tenant_id    text NOT NULL REFERENCES tenants (id),
  topic        text NOT NULL,
  title        text NOT NULL,
  body         text NOT NULL,
  source_cases text[] NOT NULL,
  author       text NOT NULL DEFAULT 'agent',
  audience     text NOT NULL DEFAULT 'internal' CHECK (audience IN ('internal', 'customer_visible')),
  status       text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'rejected')),
  reviewed_by  text,
  review_note  text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  reviewed_at  timestamptz,
  CHECK (status = 'draft' OR reviewed_by IS NOT NULL),
  CHECK (audience = 'internal' OR status = 'published')
);
ALTER TABLE kb_article_drafts ENABLE ROW LEVEL SECURITY;
ALTER TABLE kb_article_drafts FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON kb_article_drafts
  USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());
ALTER TABLE kb_article_drafts OWNER TO app_owner;
GRANT SELECT, INSERT ON kb_article_drafts TO app_runtime;
GRANT UPDATE (audience, status, reviewed_by, review_note, reviewed_at) ON kb_article_drafts TO app_runtime;
"""


def apply_schema(database):
    if not psql(database, f"SELECT 1 FROM schema_migrations WHERE name = '{SCHEMA_NAME}'"):
        psql(database, f"BEGIN;\n{SCHEMA}\nINSERT INTO schema_migrations (name) VALUES ('{SCHEMA_NAME}');\n"
                       "COMMIT;\n")


def _context(conn, tenant_id):
    conn.execute("SET LOCAL ROLE app_runtime")
    conn.execute("SELECT set_config('app.current_tenant_id', %s, true)", (tenant_id,))


def gap_topics(review, min_volume=MIN_GAP_VOLUME):
    """Knowledge-gap clusters of a weekly review (improvement.feedback.Review) worth an article."""
    return [c.category for c in review.clusters
            if taxonomy.TREE[c.category].subsystem == "retrieval" and c.volume >= min_volume]


class Refused(PermissionError):
    pass


class Articles:
    def __init__(self, connect, generate, verify_staff):
        """generate(prompt) -> (title, body); verify_staff(credential) -> Approver (roles from the staff
        directory)."""
        self.connect, self.generate, self.verify_staff = connect, generate, verify_staff

    def draft(self, tenant_id, topic, resolved_cases):
        """resolved_cases: [(case id, masked text)] that ended in a resolution."""
        if not resolved_cases:
            raise ValueError("an article is drafted from resolved cases")
        if any(find_pii(text) for _, text in resolved_cases):
            raise ValueError("case text must be tokenized before drafting")
        sources = "\n\n".join(f"[{case_id}] {text}" for case_id, text in resolved_cases)
        title, body = self.generate(
            "Write a short internal knowledge article that would have answered these resolved support cases. "
            "Use only what the cases establish; mark anything uncertain for the reviewer.\n\n" + sources)
        if find_pii(title) or find_pii(body):
            raise ValueError("the draft holds personal data")
        draft_id = str(uuid.uuid4())
        with self.connect() as conn, conn.transaction():
            _context(conn, tenant_id)
            conn.execute("INSERT INTO kb_article_drafts (id, tenant_id, topic, title, body, source_cases) "
                         "VALUES (%s, %s, %s, %s, %s, %s)",
                         (draft_id, tenant_id, topic, title, body, [case for case, _ in resolved_cases]))
        return draft_id

    def _reviewer(self, tenant_id, credential):
        approver = self.verify_staff(credential)
        if approver.tenant_id != tenant_id or approver.approver_id == AGENT:
            raise Refused("the reviewer must be a person of this tenant")
        if PUBLISHER_ROLE not in approver.roles:
            raise Refused(f"publishing needs the {PUBLISHER_ROLE} role")
        return approver

    def _decide(self, tenant_id, draft_id, credential, status, note):
        approver = self._reviewer(tenant_id, credential)
        with self.connect() as conn, conn.transaction():
            _context(conn, tenant_id)
            row = conn.execute("SELECT status, title, body, topic FROM kb_article_drafts WHERE id = %s "
                               "FOR UPDATE", (draft_id,)).fetchone()
            if row is None or row[0] != "draft":
                raise Refused("only a pending draft can be reviewed")
            conn.execute("UPDATE kb_article_drafts SET status = %s, audience = %s, reviewed_by = %s, "
                         "review_note = %s, reviewed_at = now() WHERE id = %s",
                         (status, "customer_visible" if status == "published" else "internal",
                          approver.approver_id, note, draft_id))
        return row[1:]

    def publish(self, tenant_id, draft_id, credential):
        title, body, topic = self._decide(tenant_id, draft_id, credential, "published", None)
        return Document(doc_id=f"kb-draft-{draft_id}", source_system="agent_drafts", source_kind="curated",
                        title=title, markdown=body, audience="customer_visible", provenance="agent")

    def reject(self, tenant_id, draft_id, credential, note):
        if not note or not note.strip():
            raise ValueError("a rejection says why")
        self._decide(tenant_id, draft_id, credential, "rejected", note)

    def drafts(self, tenant_id, status="draft"):
        with self.connect() as conn, conn.transaction():
            _context(conn, tenant_id)
            return conn.execute("SELECT id, topic, title, audience, status, reviewed_by FROM "
                                "kb_article_drafts WHERE status = %s ORDER BY created_at",
                                (status,)).fetchall()
