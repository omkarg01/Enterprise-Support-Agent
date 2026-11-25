"""The erasure inventory: every store that holds a user's data, how to purge it and how to prove it is gone
(FR-19 / MS-D14; NFR-5 / DP-D13, DP-D14, DP-D15). The workflow that drives it is data.erasure.

Erased, keyed on (tenant_id, principal_id):
    temporal      ticket workflow histories of the user's cases and conversations (a live turn's workflow is
                  keyed by its conversation, and its history holds the raw message)
    postgres      the case state of the user's own cases and conversations (the turn loop keys a case by its
                  conversation), the user's support tickets with their history, facts, dialogue turns /
                  summaries / pins, the event log, submitted turns, one-time codes, conversations, the user's
                  own cases and sessions, and the feedback and regression cases on their conversations
    vault         the PII values behind every token found in the user's data
    keys          the user's data keys (crypto-shredding: tool audit payloads and any copy restored from a
                  backup become unreadable)
Kept, and checked to hold nothing readable:
    decision_records    append-only audit: reasons and masked traces, scanned for raw personal data
    tool_audit_records  append-only audit: personal details only in payload_enc, unreadable once keys go
Kept by law, outside this inventory: transcript_legal_archive (2 years, GDPR Art. 17(3)(e), SG-D12); it holds
tokenized text only, and the vault values behind its tokens are erased here.
Not personal: the knowledge index and raw snapshots carry no user identity; a document is erased by id with
erase_document(), which also rewrites every raw snapshot that holds it (DP-D15).

ponytail: Langfuse / OpenTelemetry traces join the inventory when tracing is deployed (M5-01).
"""

import asyncio
import hashlib
import hmac
import io
import json
import tarfile
from datetime import datetime, timedelta, timezone

import psycopg
from temporalio import activity
from temporalio.api.common.v1 import WorkflowExecution
from temporalio.api.workflowservice.v1 import DeleteWorkflowExecutionRequest, DescribeWorkflowExecutionRequest
from temporalio.service import RPCError, RPCStatusCode
from temporalio.worker import Worker

from data.erasure import (
    BACKUP_RETENTION_DAYS,
    TASK_QUEUE,
    ErasureRequest,
    ErasureWorkflow,
    Outcome,
    Plan,
    subject_hash,
)
from data.keys import KeyShredded, decrypt_field
from data.migrate import psql
from safety.pii.tokens import TOKEN, find

STORES = ["temporal", "postgres", "vault", "keys"]
DELETE_WAIT = 30  # seconds to wait for Temporal's asynchronous deletion before retrying the activity
OWN_CONVERSATIONS = "SELECT id FROM conversations WHERE tenant_id = %(t)s AND principal_id = %(p)s"
OWN_TICKETS = "SELECT id FROM support_tickets WHERE tenant_id = %(t)s AND principal_id = %(p)s"
POSTGRES = [  # (table, predicate), children before parents; every predicate names tenant and principal
    ("case_state", f"tenant_id = %(t)s AND (case_id = ANY(%(cases)s) OR case_id IN ({OWN_CONVERSATIONS}))"),
    ("ticket_events", f"tenant_id = %(t)s AND ticket_id IN ({OWN_TICKETS})"),
    ("support_tickets", "tenant_id = %(t)s AND principal_id = %(p)s"),
    ("user_facts", "tenant_id = %(t)s AND principal_id = %(p)s"),
    ("dialogue_pins", f"tenant_id = %(t)s AND conversation_id IN ({OWN_CONVERSATIONS})"),
    ("dialogue_summaries", f"tenant_id = %(t)s AND conversation_id IN ({OWN_CONVERSATIONS})"),
    ("dialogue_turns", f"tenant_id = %(t)s AND conversation_id IN ({OWN_CONVERSATIONS})"),
    ("outbound_event_log", f"tenant_id = %(t)s AND conversation_id IN ({OWN_CONVERSATIONS})"),
    ("conversation_messages", f"tenant_id = %(t)s AND conversation_id IN ({OWN_CONVERSATIONS})"),
    ("regression_cases", f"tenant_id = %(t)s AND conversation_id IN ({OWN_CONVERSATIONS})"),
    ("feedback_events", f"tenant_id = %(t)s AND conversation_id IN ({OWN_CONVERSATIONS})"),
    ("turns", "tenant_id = %(t)s AND principal_id = %(p)s"),
    ("otp_challenges", "tenant_id = %(t)s AND principal_id = %(p)s"),
    ("conversations", "tenant_id = %(t)s AND principal_id = %(p)s"),
    ("cases", "tenant_id = %(t)s AND id = ANY(%(cases)s)"),
    ("sessions", "tenant_id = %(t)s AND principal_id = %(p)s"),
]

SCHEMA_NAME = "lifecycle_0001_erasure.sql"
SCHEMA = """
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'lifecycle_service') THEN
    CREATE ROLE lifecycle_service NOLOGIN;
  END IF;
END $$;

-- Who asked, when, and the signed receipt. The user is named only by a hash of (tenant, principal).
CREATE TABLE erasure_ledger (
  job_id       text PRIMARY KEY,
  tenant_id    text NOT NULL REFERENCES tenants (id),
  subject_hash text NOT NULL,
  requested_by text NOT NULL,
  reason       text NOT NULL,
  status       text NOT NULL DEFAULT 'IN_PROGRESS' CHECK (status IN ('IN_PROGRESS', 'COMPLETED', 'FAILED')),
  receipt      jsonb,
  created_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
  completed_at timestamptz
);
ALTER TABLE erasure_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE erasure_ledger FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON erasure_ledger
  USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());
ALTER TABLE erasure_ledger OWNER TO app_owner;

-- The event log only lets anyone delete expired events; erasure may delete a tenant's fresh ones too.
CREATE POLICY erasure ON outbound_event_log FOR DELETE TO lifecycle_service
  USING (tenant_id = current_tenant());
"""
GRANTS = """
GRANT SELECT, INSERT, UPDATE ON erasure_ledger TO lifecycle_service;
GRANT SELECT ON decision_records, tool_audit_records TO lifecycle_service;
GRANT SELECT, DELETE ON user_facts, dialogue_pins, dialogue_summaries, dialogue_turns, outbound_event_log,
  conversation_messages, turns, otp_challenges, conversations, cases, sessions, feedback_events,
  regression_cases TO lifecycle_service;
"""


def apply_schema(database):
    """Needs every inventoried module's schema first; the grants are re-applied on every call."""
    if not psql(database, f"SELECT 1 FROM schema_migrations WHERE name = '{SCHEMA_NAME}'"):
        psql(database, f"BEGIN;\n{SCHEMA}\nINSERT INTO schema_migrations (name) VALUES ('{SCHEMA_NAME}');\n"
                       "COMMIT;\n")
    psql(database, GRANTS)


def context(conn, tenant_id, principal_id):
    conn.execute("SET LOCAL ROLE lifecycle_service")
    conn.execute("SELECT set_config('app.current_tenant_id', %s, true), "
                 "set_config('app.current_principal_id', %s, true)", (tenant_id, principal_id))


def receipt(outcome, request, signing_key, completed_at):
    body = {"job_id": outcome.job_id, "tenant_id": request.tenant_id,
            "subject": subject_hash(request.tenant_id, request.principal_id), "status": outcome.status,
            "purged": outcome.purged, "residual": outcome.residual, "completed_at": completed_at.isoformat(),
            "backups_expire_by": (completed_at + timedelta(days=BACKUP_RETENTION_DAYS)).isoformat()}
    canonical = json.dumps(body, sort_keys=True).encode()
    return {**body, "signature": hmac.new(signing_key, canonical, hashlib.sha256).hexdigest()}


def verify_receipt(signed, signing_key):
    body = {k: v for k, v in signed.items() if k != "signature"}
    expected = hmac.new(signing_key, json.dumps(body, sort_keys=True).encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signed["signature"])


def _pack(members):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name in sorted(members):
            info = tarfile.TarInfo(name)
            info.size = len(members[name])
            tar.addfile(info, io.BytesIO(members[name]))
    return buffer.getvalue()


def erase_document(ingestion, scope, doc_id, reason):
    """Erase one knowledge document: tombstone, index, and every raw snapshot that holds it (DP-D15).
    Returns how many snapshot versions were rewritten."""
    ingestion.purge(scope, doc_id, reason)
    if ingestion.snapshots is None:
        return 0
    rewritten, name = 0, f"{doc_id}.md"
    for key in ingestion.snapshots.keys(f"snapshots/{scope}/"):
        with tarfile.open(fileobj=io.BytesIO(ingestion.snapshots.get(key)), mode="r:gz") as tar:
            members = {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
        if name in members:
            del members[name]
            ingestion.snapshots.delete(key)  # snapshots are write-once, so replace = delete + put
            ingestion.snapshots.put(key, _pack(members))
            rewritten += 1
    return rewritten


class ErasureActivities:
    def __init__(self, main_dsn, vault_dsn, keys, client, signing_key, namespace="default"):
        """keys(): a data.keys.KeyStore on its own connection; client: temporalio Client."""
        self.main_dsn, self.vault_dsn, self.keys, self.client = main_dsn, vault_dsn, keys, client
        self.signing_key, self.namespace = signing_key, namespace

    def _main(self):
        return psycopg.connect(self.main_dsn, autocommit=True)

    @activity.defn(name="erasure_stores")
    async def stores(self) -> list[str]:
        return STORES

    @activity.defn(name="erasure_open")
    async def open(self, job_id: str, request: ErasureRequest) -> None:
        def run():
            with self._main() as conn, conn.transaction():
                context(conn, request.tenant_id, request.principal_id)
                subject = subject_hash(request.tenant_id, request.principal_id)
                conn.execute("INSERT INTO erasure_ledger (job_id, tenant_id, subject_hash, requested_by, "
                             "reason) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (job_id) DO UPDATE SET "
                             "status = 'IN_PROGRESS', receipt = NULL, completed_at = NULL",
                             (job_id, request.tenant_id, subject, request.requested_by, request.reason))
        await asyncio.to_thread(run)

    @activity.defn(name="erasure_collect")
    async def collect(self, request: ErasureRequest) -> Plan:
        return await asyncio.to_thread(self._collect, request)

    def _collect(self, request):
        t, p = request.tenant_id, request.principal_id
        with self._main() as conn, conn.transaction():
            context(conn, t, p)
            args = {"t": t, "p": p}
            conversations = [r[0] for r in conn.execute(OWN_CONVERSATIONS, args)]
            cases = [r[0] for r in conn.execute(
                "SELECT DISTINCT c.case_id FROM conversations c WHERE c.tenant_id = %(t)s "
                "AND c.principal_id = %(p)s AND c.case_id IS NOT NULL AND NOT EXISTS "
                "(SELECT 1 FROM conversations o WHERE o.case_id = c.case_id AND o.principal_id <> %(p)s)",
                args)]
            texts = [r[0] for r in conn.execute(" UNION ALL ".join(
                f"SELECT {column} FROM {table} WHERE conversation_id IN ({OWN_CONVERSATIONS})"
                for table, column in (("dialogue_turns", "content"), ("dialogue_summaries", "summary"),
                                      ("dialogue_pins", "content"), ("outbound_event_log", "data::text"),
                                      ("conversation_messages", "text"), ("feedback_events", "comment"),
                                      ("regression_cases", "question || ' ' || answer"))),
                args)]
            texts += [r[0] for r in conn.execute(  # ticket texts are masked: their tokens' values go too
                f"SELECT description FROM support_tickets WHERE tenant_id = %(t)s AND principal_id = %(p)s "
                f"UNION ALL SELECT text FROM ticket_events WHERE ticket_id IN ({OWN_TICKETS})", args)]
            facts = conn.execute("SELECT id, statement_enc FROM user_facts WHERE principal_id = %(p)s",
                                 args).fetchall()
        store = self.keys()
        for fact_id, sealed in facts:
            try:
                texts.append(decrypt_field(store, t, p, f"fact:{fact_id}", bytes(sealed)).decode())
            except KeyShredded:
                break
        tokens = sorted({token for text in texts for token in TOKEN.findall(text or "")})
        return Plan(cases, conversations, tokens)

    @activity.defn(name="erasure_purge")
    async def purge(self, store: str, request: ErasureRequest, plan: Plan) -> int:
        if store == "temporal":
            return await self._purge_temporal(request, plan)
        return await asyncio.to_thread(getattr(self, f"_purge_{store}"), request, plan)

    def _ticket_ids(self, request, plan):
        """Linked cases' workflows and the conversation-keyed ones the live turns run on (their histories hold
        the customer's raw messages)."""
        return [f"ticket:{request.tenant_id}:{key}" for key in (*plan.case_ids, *plan.conversation_ids)]

    async def _purge_temporal(self, request, plan):
        """Delete the ticket workflows (running ones are terminated) and wait until Temporal has done it;
        deletion is asynchronous there. Still present after DELETE_WAIT: raise, so the activity retries."""
        deleted = 0
        for workflow_id in self._ticket_ids(request, plan):
            try:
                await self.client.workflow_service.delete_workflow_execution(DeleteWorkflowExecutionRequest(
                    namespace=self.namespace, workflow_execution=WorkflowExecution(workflow_id=workflow_id)))
                deleted += 1
            except RPCError as error:
                if error.status != RPCStatusCode.NOT_FOUND:
                    raise
        for _ in range(DELETE_WAIT):
            if not await self._temporal_left(request, plan):
                return deleted
            await asyncio.sleep(1)
        raise RuntimeError("Temporal has not finished deleting the ticket workflows yet")

    async def _temporal_left(self, request, plan):
        left = 0
        for workflow_id in self._ticket_ids(request, plan):
            try:
                await self.client.workflow_service.describe_workflow_execution(
                    DescribeWorkflowExecutionRequest(namespace=self.namespace,
                                                     execution=WorkflowExecution(workflow_id=workflow_id)))
                left += 1
            except RPCError as error:
                if error.status != RPCStatusCode.NOT_FOUND:
                    raise
        return left

    def _purge_postgres(self, request, plan):
        args = {"t": request.tenant_id, "p": request.principal_id, "cases": plan.case_ids}
        with self._main() as conn, conn.transaction():
            context(conn, request.tenant_id, request.principal_id)
            return sum(conn.execute(f"DELETE FROM {table} WHERE {where}", args).rowcount
                       for table, where in POSTGRES)

    def _vault(self):
        return psycopg.connect(self.vault_dsn, autocommit=True)

    def _purge_vault(self, request, plan):
        with self._vault() as conn, conn.transaction():
            conn.execute("SET LOCAL ROLE vault_service")
            conn.execute("SELECT set_config('app.current_tenant_id', %s, true)", (request.tenant_id,))
            return conn.execute("DELETE FROM pii_tokens WHERE token = ANY(%s)", (plan.tokens,)).rowcount

    def _purge_keys(self, request, plan):
        return self.keys().shred(request.tenant_id, request.principal_id)

    @activity.defn(name="erasure_verify")
    async def verify(self, request: ErasureRequest, plan: Plan) -> dict[str, int]:
        residual = await asyncio.to_thread(self._verify, request, plan)
        residual["temporal"] = await self._temporal_left(request, plan)
        return residual

    def _verify(self, request, plan):
        t, p = request.tenant_id, request.principal_id
        args = {"t": t, "p": p, "cases": plan.case_ids}
        residual = {}
        with self._main() as conn, conn.transaction():
            context(conn, t, p)
            residual["postgres"] = sum(conn.execute(f"SELECT count(*) FROM {table} WHERE {where}",
                                                    args).fetchone()[0] for table, where in POSTGRES)
            decisions = conn.execute("SELECT reason, checks::text, trace::text FROM decision_records "
                                     "WHERE conversation_id = ANY(%s)", (plan.conversation_ids,)).fetchall()
            audits = conn.execute("SELECT id, payload_enc FROM tool_audit_records WHERE user_id = %s "
                                  "AND payload_enc IS NOT NULL", (p,)).fetchall()
        residual["decision_records"] = sum(1 for row in decisions if any(find(text or "") for text in row))
        residual["keys"] = self._key_rows(t, p)
        # audit payloads are sealed under the user's keys: readable exactly as long as a key is left
        residual["tool_audit_records"] = len(audits) if residual["keys"] else 0
        with self._vault() as conn, conn.transaction():
            conn.execute("SET LOCAL ROLE vault_service")
            conn.execute("SELECT set_config('app.current_tenant_id', %s, true)", (t,))
            residual["vault"] = conn.execute("SELECT count(*) FROM pii_tokens WHERE token = ANY(%s)",
                                             (plan.tokens,)).fetchone()[0]
        return residual

    def _key_rows(self, tenant_id, principal_id):
        store = self.keys()
        with store.conn.transaction():
            store.conn.execute("SET LOCAL ROLE key_service")
            store.conn.execute("SELECT set_config('app.current_tenant_id', %s, true)", (tenant_id,))
            return store.conn.execute("SELECT count(*) FROM user_keys WHERE user_id = %s",
                                      (principal_id,)).fetchone()[0]

    @activity.defn(name="erasure_close")
    async def close(self, outcome: Outcome, request: ErasureRequest) -> None:
        def run():
            completed_at = datetime.now(timezone.utc)
            signed = receipt(outcome, request, self.signing_key, completed_at)
            with self._main() as conn, conn.transaction():
                context(conn, request.tenant_id, request.principal_id)
                conn.execute("UPDATE erasure_ledger SET status = %s, receipt = %s, completed_at = %s "
                             "WHERE job_id = %s", (outcome.status, json.dumps(signed), completed_at,
                                                   outcome.job_id))
        await asyncio.to_thread(run)


def worker(client, activities, task_queue=TASK_QUEUE):
    return Worker(client, task_queue=task_queue, workflows=[ErasureWorkflow],
                  activities=[activities.stores, activities.open, activities.collect, activities.purge,
                              activities.verify, activities.close])
