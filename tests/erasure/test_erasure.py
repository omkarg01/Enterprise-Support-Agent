"""M2-03: the erasure workflow across every store in the inventory, back-office erasure and correction, TTLs,
document erasure with snapshot rewriting, and the backup horizon (FR-19, NFR-5, AC-12).

Runs against the compose Postgres (main, vault and key-store databases), OpenBao, Temporal and Qdrant."""

import asyncio
import json
import subprocess
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import psycopg
import pytest
from qdrant_client import QdrantClient
from temporalio.api.common.v1 import WorkflowExecution
from temporalio.api.workflowservice.v1 import DescribeWorkflowExecutionRequest
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

from channels import sessions
from channels.api import turns
from channels.identity import otp
from core.orchestrator import case_state
from core.orchestrator.case_state import PostgresStore
from core.orchestrator.statechart import build, thread
from core.secrets import OpenBao
from data import events
from data.erasure import ErasureRequest, start_erasure, subject_hash
from data.keys import KeyShredded, KeyStore, OpenBaoTransit, decrypt_field, encrypt_field
from data.migrate import migrate, psql
from data.storage import LocalStore, read_snapshot
from evals import regressions
from hitl import tickets
from hitl.tickets import Tickets
from improvement import feedback
from knowledge.index.store import Index
from knowledge.ingest import pipeline as ingest
from knowledge.ingest.pipeline import Document, Ingestion
from memory.conversation import dialogue
from memory.conversation.dialogue import Dialogue
from memory.facts import store as fact_store
from memory.facts.store import FactStore
from memory.lifecycle import inventory, retention
from memory.lifecycle.backoffice import correct, forget, remembered, request_erasure
from memory.lifecycle.inventory import ErasureActivities, erase_document, verify_receipt, worker
from safety.governance import records
from safety.governance.records import DecisionLog
from safety.pii.tokens import TOKEN, Tokenizer
from safety.pii.vault import Vault
from tests.knowledge.ingest.test_ingest import DIMENSIONS, embed

ROOT = Path(__file__).resolve().parents[2]
MAIN, VAULT, KEYS = "test_erasure", "test_erasure_vault", "test_erasure_keys"
DSN = "postgresql://agent:change-me@127.0.0.1:5432/{}"  # credentials from .env.example
SIGNING_KEY = b"test-erasure-signing-key"
TOKENS = Tokenizer(b"test-erasure-token-key")


def connect(database):
    return psycopg.connect(DSN.format(database), autocommit=True)


@pytest.fixture(scope="module")
def stack():
    subprocess.run(["docker", "compose", "-f", "deploy/compose.yaml", "--env-file", ".env.example", "up",
                    "-d", "--wait", "postgres", "openbao", "temporal", "qdrant"], cwd=ROOT, check=True,
                   capture_output=True)
    for name, kind in ((MAIN, "main"), (VAULT, "vault"), (KEYS, "keys")):
        psql("postgres", f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        psql("postgres", f'CREATE DATABASE "{name}"')
        migrate(kind, name)
    for module in (sessions, turns, otp, events, dialogue, fact_store, records, case_state, tickets,
                   feedback, regressions, inventory, retention):
        module.apply_schema(MAIN)
    psql(MAIN, "INSERT INTO tenants (id, name, region) "
               "VALUES ('t_us', 'Acme', 'US'), ('t_eu', 'Globex', 'EU')")
    asyncio.run(stop_leftover_erasures())
    bao = OpenBao("http://127.0.0.1:8200", "change-me")
    for _ in range(30):
        try:
            bao.enable_transit()
            break
        except httpx.HTTPError:
            time.sleep(1)
    yield {"bao": bao, "saver": PostgresStore(lambda: connect(MAIN))}


async def stop_leftover_erasures():
    """An interrupted session can leave a test user's erasure running, and a user has one at a time."""
    client = await Client.connect("127.0.0.1:7233")
    query = "WorkflowType = 'ErasureWorkflow' AND ExecutionStatus = 'Running'"
    async for execution in client.list_workflows(query):
        if execution.task_queue.startswith("erasure-") or execution.task_queue == "no-worker-here":
            handle = client.get_workflow_handle(execution.id, run_id=execution.run_id)
            await handle.terminate("stale test run")


def key_store(stack):
    return KeyStore(connect(KEYS), OpenBaoTransit(stack["bao"]))


def seed(stack, principal, tenant="t_us"):
    """Give a user data in every store; returns what an erasure must remove."""
    keys = key_store(stack)
    vault = Vault(TOKENS, lambda: connect(VAULT), keys)
    phone = f"+44 20 7946 {1000 + len(principal)}"
    masked, stored = vault.tokenize(tenant, f"I'm {principal}@acme.com, call {phone}")
    assert stored
    with connect(MAIN) as conn:
        session = sessions.start(conn, tenant, principal, "T2_SSO")
        conversation = f"conv-{principal}-{uuid.uuid4().hex[:6]}"
        sessions.open_conversation(conn, tenant, principal, session, conversation)
        case = sessions.link_case(conn, tenant, conversation)
        memory = Dialogue(conn, None, lambda previous, turns_, pins: "Summary of " + turns_[0]["content"])
        memory.append(tenant, conversation, 1, "user", f"{masked} about INV-9821")
        for seq in range(2, 11):
            memory.append(tenant, conversation, seq, "assistant" if seq % 2 == 0 else "user", f"turn {seq}")
        assert memory.compact(tenant, conversation)
        facts = FactStore(conn, keys)
        statement = f"Prefers e-mail at {TOKEN.findall(masked)[0]}"
        facts.add(tenant, principal, statement, "comm_style", 5, [1.0, 0.0])
        DecisionLog(lambda: connect(MAIN)).record(tenant, conversation, "answer", "We found the answer.",
                                                  ["Content safety"], {"message": masked})
    desk = Tickets(lambda: connect(MAIN))  # a raised ticket with a note, both holding the masked message
    ticket = desk.confirm(tenant, principal, desk.propose(
        tenant, principal, conversation, "k1", category="refund", subject="Duplicate charge",
        queue="queue_billing", reason="refund request", description=masked).id)
    desk.add_note(tenant, principal, ticket.id, "customer", masked)
    payload = encrypt_field(keys, tenant, principal, "tool_audit:1", b'{"email": "x@acme.com"}')
    psql(MAIN, f"""
        INSERT INTO turns (tenant_id, principal_id, conversation_id, idempotency_key)
          VALUES ('{tenant}', '{principal}', '{conversation}', 'k1');
        INSERT INTO otp_challenges (tenant_id, principal_id, salt, code_hash, expires_at)
          VALUES ('{tenant}', '{principal}', '\\x01', '\\x02', now() + interval '5 minutes');
        INSERT INTO outbound_event_log (tenant_id, conversation_id, event_type, data)
          VALUES ('{tenant}', '{conversation}', 'final', '{json.dumps({"text": masked}).replace("'", "''")}');
        INSERT INTO conversation_messages (tenant_id, conversation_id, role, text)
          VALUES ('{tenant}', '{conversation}', 'user', 'my email is x@acme.com');
        INSERT INTO tool_audit_records
            (tenant_id, conversation_id, user_id, tool, action, outcome, payload_enc)
          VALUES ('{tenant}', '{conversation}', '{principal}', 'crm', 'update', 'ok', '\\x{payload.hex()}');
    """)
    graph = build({"triage": lambda state: {"intent": "UNKNOWN", "acuity": 3, "confidence": 0.0}},
                  stack["saver"])
    for key in (case, conversation):  # a linked case, and a case the turn loop keys by its conversation
        graph.invoke({"tenant_id": tenant, "case_id": key, "session_id": session,
                      "event": {"type": "USER_MESSAGE", "text": masked}}, thread(tenant, key))

    async def ticket():
        client = await Client.connect("127.0.0.1:7233")
        for key in (case, conversation):  # a linked case's ticket, and the live turns' conversation one
            await client.start_workflow("TicketWorkflow", id=f"ticket:{tenant}:{key}",
                                        task_queue="no-worker-here")
    asyncio.run(ticket())
    return {"conversation": conversation, "case": case, "tokens": TOKEN.findall(masked)}


def erase(stack, principal, activities_class=ErasureActivities, tenant="t_us"):
    async def go():
        client = await Client.connect("127.0.0.1:7233")
        activities = activities_class(DSN.format(MAIN), DSN.format(VAULT), lambda: key_store(stack), client,
                                      SIGNING_KEY)
        queue = f"erasure-{uuid.uuid4().hex}"
        async with worker(client, activities, queue):
            handle = await start_erasure(client, ErasureRequest(tenant, principal, "admin-ada"), queue)
            return await handle.result()
    return asyncio.run(go())


def counts(principal, seeded, tenant="t_us"):
    """What each store still holds for the user, asked directly."""
    conversation, case = seeded["conversation"], seeded["case"]
    rows = psql(MAIN, f"""
        SELECT (SELECT count(*) FROM sessions WHERE principal_id = '{principal}')
             + (SELECT count(*) FROM conversations WHERE principal_id = '{principal}')
             + (SELECT count(*) FROM cases WHERE id = '{case}')
             + (SELECT count(*) FROM dialogue_turns WHERE conversation_id = '{conversation}')
             + (SELECT count(*) FROM dialogue_summaries WHERE conversation_id = '{conversation}')
             + (SELECT count(*) FROM dialogue_pins WHERE conversation_id = '{conversation}')
             + (SELECT count(*) FROM outbound_event_log WHERE conversation_id = '{conversation}')
             + (SELECT count(*) FROM conversation_messages WHERE conversation_id = '{conversation}')
             + (SELECT count(*) FROM turns WHERE principal_id = '{principal}')
             + (SELECT count(*) FROM otp_challenges WHERE principal_id = '{principal}')
             + (SELECT count(*) FROM user_facts WHERE principal_id = '{principal}')
             + (SELECT count(*) FROM support_tickets WHERE principal_id = '{principal}')
             + (SELECT count(*) FROM ticket_events WHERE ticket_id IN
                 (SELECT id FROM support_tickets WHERE principal_id = '{principal}')),
               (SELECT count(*) FROM case_state
                 WHERE tenant_id = '{tenant}' AND case_id IN ('{case}', '{conversation}'))
    """)
    tokens = "', '".join(seeded["tokens"])
    vault = psql(VAULT, f"SELECT count(*) FROM pii_tokens WHERE token IN ('{tokens}')")
    keys = psql(KEYS, f"SELECT count(*) FROM user_keys WHERE user_id = '{principal}'")
    return {"postgres": int(rows[0][0]), "case_state": int(rows[0][1]), "vault": int(vault[0][0]),
            "keys": int(keys[0][0])}


def temporal_has(workflow_id):
    async def go():
        client = await Client.connect("127.0.0.1:7233")
        try:
            await client.workflow_service.describe_workflow_execution(DescribeWorkflowExecutionRequest(
                namespace="default", execution=WorkflowExecution(workflow_id=workflow_id)))
            return True
        except RPCError as error:
            if error.status == RPCStatusCode.NOT_FOUND:
                return False
            raise
    return asyncio.run(go())


def test_erasure_completes_and_no_store_returns_readable_personal_data(stack):
    """AC-12, with a second user in the same tenant who must keep everything."""
    sarah, bob = seed(stack, "sarah"), seed(stack, "bob")
    assert all(counts("sarah", sarah).values()) and temporal_has(f"ticket:t_us:{sarah['case']}")
    keys = key_store(stack)
    sealed = psql(MAIN, "SELECT encode(payload_enc, 'hex') FROM tool_audit_records WHERE user_id = 'sarah'")

    outcome = erase(stack, "sarah")

    assert outcome.status == "COMPLETED" and not any(outcome.residual.values())
    assert set(outcome.residual) == {"temporal", "postgres", "decision_records",
                                     "tool_audit_records", "vault", "keys"}
    assert counts("sarah", sarah) == {"postgres": 0, "case_state": 0, "vault": 0, "keys": 0}
    assert not temporal_has(f"ticket:t_us:{sarah['case']}")
    assert not temporal_has(f"ticket:t_us:{sarah['conversation']}")
    with pytest.raises(KeyShredded):  # the audit payload (and any backup copy) can no longer be opened
        decrypt_field(keys, "t_us", "sarah", "tool_audit:1", bytes.fromhex(sealed[0][0]))
    retained = psql(MAIN, "SELECT trace::text FROM decision_records "
                          f"WHERE conversation_id = '{sarah['conversation']}'")
    assert retained and "sarah@" not in retained[0][0]  # the audit row stays, tokens only

    assert all(counts("bob", bob).values()) and temporal_has(f"ticket:t_us:{bob['case']}")
    assert temporal_has(f"ticket:t_us:{bob['conversation']}")
    with connect(MAIN) as conn:
        assert [f.statement for f in FactStore(conn, keys).as_of("t_us", "bob")] == [
            f"Prefers e-mail at {bob['tokens'][0]}"]


def test_the_ledger_keeps_a_signed_receipt_without_the_users_id(stack):
    seed(stack, "carol")
    outcome = erase(stack, "carol")
    status, receipt, requested_by = psql(
        MAIN, "SELECT status, receipt::text, requested_by FROM erasure_ledger "
              f"WHERE job_id = '{outcome.job_id}'")[0]
    signed = json.loads(receipt)
    assert status == "COMPLETED" and requested_by == "admin-ada" and verify_receipt(signed, SIGNING_KEY)
    assert "carol" not in receipt and signed["subject"] == subject_hash("t_us", "carol")
    assert signed["purged"]["postgres"] > 0 and signed["status"] == "COMPLETED"
    completed = datetime.fromisoformat(signed["completed_at"])
    assert datetime.fromisoformat(signed["backups_expire_by"]) == completed + timedelta(days=35)
    assert not verify_receipt({**signed, "status": "FAILED"}, SIGNING_KEY)


class FlakyVault(ErasureActivities):
    """The vault is down for the first purge attempt."""
    failed = []

    def _purge_vault(self, request, plan):
        if not FlakyVault.failed:
            FlakyVault.failed.append(True)
            raise psycopg.OperationalError("vault unreachable")
        return super()._purge_vault(request, plan)


class BrokenPostgres(ErasureActivities):
    """A purge handler that silently deletes nothing: verification must catch it."""

    def _purge_postgres(self, request, plan):
        return 0


def test_a_store_that_is_down_is_retried_not_skipped(stack):
    dave = seed(stack, "dave")
    outcome = erase(stack, "dave", FlakyVault)
    assert FlakyVault.failed and outcome.status == "COMPLETED" and counts("dave", dave)["vault"] == 0


def test_a_residue_is_reported_as_failed_never_as_complete(stack):
    seed(stack, "erin")
    outcome = erase(stack, "erin", BrokenPostgres)
    assert outcome.status == "FAILED" and outcome.residual["postgres"] > 0
    assert psql(MAIN, f"SELECT status FROM erasure_ledger WHERE job_id = '{outcome.job_id}'") == [["FAILED"]]


def test_raw_personal_data_left_in_a_kept_audit_row_fails_the_erasure(stack):
    jack = seed(stack, "jack")
    psql(MAIN, "INSERT INTO decision_records (tenant_id, conversation_id, decision, reason, trace) "
               f"VALUES ('t_us', '{jack['conversation']}', 'answer', 'x', '{{\"to\": \"jack@acme.com\"}}')")
    outcome = erase(stack, "jack")
    assert outcome.status == "FAILED" and outcome.residual["decision_records"] == 1


def test_erasure_stays_inside_the_tenant(stack):
    us, eu = seed(stack, "frank"), seed(stack, "frank", tenant="t_eu")
    erase(stack, "frank", tenant="t_eu")
    assert counts("frank", eu, tenant="t_eu")["case_state"] == 0
    kept = psql(MAIN, "SELECT count(*) FROM user_facts WHERE principal_id = 'frank' AND tenant_id = 't_us'")
    assert kept == [["1"]]
    assert counts("frank", us)["case_state"] == 2 and temporal_has(f"ticket:t_us:{us['case']}")


def test_back_office_sees_corrects_and_forgets_facts(stack):
    keys = key_store(stack)
    with connect(MAIN) as conn:
        facts = FactStore(conn, keys)
        first = facts.add("t_us", "gina", "Runs Kubernetes version 1.28", "technical_env", 6, [1.0, 0.0])
        facts.add("t_us", "gina", "Prefers dark mode", "user_preference", 2, [0.0, 1.0])
        fixed = correct(facts, lambda texts: [[1.0, 0.1]], "t_us", "gina", first,
                        "Runs Kubernetes version 1.29")
        assert sorted(f["statement"] for f in remembered(facts, "t_us", "gina")) == [
            "Prefers dark mode", "Runs Kubernetes version 1.29"]
        with pytest.raises(LookupError):
            correct(facts, embed, "t_us", "gina", first, "again")  # no longer current
        assert forget(conn, "t_us", "bob", fixed) == 0  # not bob's fact
        assert forget(conn, "t_us", "gina", fixed) == 2  # the correction and the version it replaced
        assert [f["statement"] for f in remembered(facts, "t_us", "gina")] == ["Prefers dark mode"]

    async def go():
        client = await Client.connect("127.0.0.1:7233")
        handle = await request_erasure(client, "t_us", "gina", "admin-ada", task_queue="no-worker-here")
        description = await handle.describe()
        await handle.terminate()
        return handle.id, description.status.name
    workflow_id, status = asyncio.run(go())
    assert workflow_id.startswith("erasure:t_us:") and "gina" not in workflow_id and status == "RUNNING"


def test_retention_expires_each_memory_type_on_its_own_clock(stack):
    keys = key_store(stack)
    hank = seed(stack, "hank")
    with connect(MAIN) as conn:
        facts = FactStore(conn, keys)
        old = facts.add("t_us", "hank", "Uses Jenkins", "technical_env", 4, [1.0, 0.0])
        facts.add("t_us", "hank", "Uses GitHub Actions", "technical_env", 4, [1.0, 0.1], replaces=old)
        now = datetime.now(timezone.utc)
        nothing = {"facts": 0, "case_state": 0, "tickets": 0, "conversations": 0}
        assert retention.sweep(conn, "t_us", now) == nothing
        psql(MAIN, f"UPDATE cases SET status = 'resolved' WHERE id = '{hank['case']}'")
        assert retention.sweep(conn, "t_us", now + timedelta(days=29))["case_state"] == 0
        assert retention.sweep(conn, "t_us", now + timedelta(days=31))["case_state"] > 0
        assert counts("hank", hank)["case_state"] == 1  # the conversation's state lives as long as it does
        swept = retention.sweep(conn, "t_us", now + timedelta(days=366))
        assert swept["facts"] >= 3  # hank's two-version lineage and his other fact (and other users' facts)
        assert psql(MAIN, "SELECT count(*) FROM user_facts WHERE principal_id = 'hank'") == [["0"]]
        turns_left = f"SELECT count(*) FROM dialogue_turns WHERE conversation_id = '{hank['conversation']}'"
        assert psql(MAIN, turns_left) != [["0"]]
        assert retention.sweep(conn, "t_us", now + timedelta(days=731))["conversations"] > 0
        assert psql(MAIN, turns_left) == [["0"]] and counts("hank", hank)["case_state"] == 0
        hank_tickets = "SELECT count(*) FROM support_tickets WHERE principal_id = 'hank'"
        assert psql(MAIN, hank_tickets) == [["0"]]


def test_a_closed_case_gets_its_close_time_and_loses_it_on_reopen(stack):
    ivy = seed(stack, "ivy")
    psql(MAIN, f"UPDATE cases SET status = 'resolved' WHERE id = '{ivy['case']}'")
    assert psql(MAIN, f"SELECT closed_at IS NOT NULL FROM cases WHERE id = '{ivy['case']}'") == [["t"]]
    psql(MAIN, f"UPDATE cases SET status = 'open' WHERE id = '{ivy['case']}'")
    assert psql(MAIN, f"SELECT closed_at IS NULL FROM cases WHERE id = '{ivy['case']}'") == [["t"]]


def test_an_erased_document_leaves_the_index_and_every_raw_snapshot(stack, tmp_path):
    ingest.apply_schema(MAIN)
    client, namespace = QdrantClient(url="http://127.0.0.1:6333"), f"x{uuid.uuid4().hex[:8]}_"
    snapshots = LocalStore(tmp_path)
    docs = [Document(doc_id=d, source_system="tickets", source_kind="ticket", title=d,
                     markdown=f"# {d}\n\nTicket text for {d}.") for d in ("t-1", "t-2")]
    try:
        with connect(MAIN) as conn:
            loader = Ingestion(Index(client, DIMENSIONS, namespace), embed, conn, snapshots)
            loader.run_batch("t_us", "tickets", docs, date(2026, 10, 1))
            loader.run_batch("t_us", "tickets", docs, date(2026, 10, 2))
            assert erase_document(loader, "t_us", "t-1", "GDPR erasure") == 2
            assert loader.index.count("t_us", "t-1") == 0 and loader.index.count("t_us", "t-2") > 0
            for key in snapshots.keys("snapshots/t_us/"):
                assert set(read_snapshot(snapshots, key)) == {"t-2.md"}
            loader.run_batch("t_us", "tickets", docs, date(2026, 10, 3))  # the source still has it
            assert loader.index.count("t_us", "t-1") == 0  # the tombstone keeps it out
    finally:
        for item in client.get_collections().collections:
            if item.name.startswith(namespace):
                client.delete_collection(item.name)
