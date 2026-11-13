"""M1-10: labels, masking, tenant collections, copied ACLs, nightly batch, instant purge and the post-batch
audit (FR-10, FR-11, FR-12). Runs against the compose Qdrant and Postgres."""

import hashlib
import math
import subprocess
import uuid
from datetime import date
from pathlib import Path

import psycopg
import pytest
from qdrant_client import QdrantClient, models

from data.migrate import migrate, psql
from data.storage import LocalStore, latest_snapshot
from knowledge.index.store import PAYLOAD_INDEXES, Index, collection, collections_for, doc_filter
from knowledge.ingest import pipeline
from knowledge.ingest.pipeline import Document, Ingestion

ROOT = Path(__file__).resolve().parents[3]
DB = "test_knowledge"
DSN = f"postgresql://agent:change-me@127.0.0.1:5432/{DB}"  # credentials from .env.example
DIMENSIONS = 64


def embed(texts):
    """Deterministic bag-of-words hashing embedder: enough to test ingestion, not retrieval quality."""
    vectors = []
    for text in texts:
        vector = [0.0] * DIMENSIONS
        for word in text.lower().split():
            vector[int(hashlib.sha256(word.encode()).hexdigest(), 16) % DIMENSIONS] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        vectors.append([v / norm for v in vector])
    return vectors


@pytest.fixture(scope="module")
def stack():
    subprocess.run(["docker", "compose", "-f", "deploy/compose.yaml", "--env-file", ".env.example",
                    "up", "-d", "--wait", "postgres", "qdrant"], cwd=ROOT, check=True, capture_output=True)
    psql("postgres", f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)')
    psql("postgres", f'CREATE DATABASE "{DB}"')
    migrate("main", DB)
    pipeline.apply_schema(DB)
    client = QdrantClient(url="http://127.0.0.1:6333")
    namespace = f"t{uuid.uuid4().hex[:8]}_"
    yield client, namespace
    for item in client.get_collections().collections:
        if item.name.startswith(namespace):
            client.delete_collection(item.name)


@pytest.fixture
def ingestion(stack, tmp_path):
    client, namespace = stack
    with psycopg.connect(DSN, autocommit=True) as conn:
        yield Ingestion(Index(client, DIMENSIONS, namespace), embed, conn, snapshots=LocalStore(tmp_path))


def doc(doc_id, text="VPN error E-1042 means the certificate expired. Renew it from the admin console.",
        source="kb", **labels):
    return Document(doc_id=doc_id, source_system=source, source_kind=labels.pop("source_kind", "curated"),
                    title=doc_id, markdown=f"# {doc_id}\n\n{text}", **labels)


def payloads(ingestion, scope, doc_id):
    name = collection(scope, ingestion.index.namespace)
    points, _ = ingestion.index.client.scroll(name, limit=100, with_payload=True,
                                              scroll_filter=doc_filter(doc_id))
    return [p.payload for p in points]


def test_chunks_are_internal_by_default_and_carry_their_labels(ingestion):
    labelled = doc("kb-2", audience="customer_visible", provenance="agent")
    ingestion.run_batch("public", "kb", [doc("kb-1"), labelled])
    first, second = payloads(ingestion, "public", "kb-1")[0], payloads(ingestion, "public", "kb-2")[0]
    assert (first["audience"], first["provenance"], first["source_kind"]) == ("internal", "human", "curated")
    assert (second["audience"], second["provenance"]) == ("customer_visible", "agent")
    assert first["parent_text"].startswith("VPN error") and first["parent_id"] == "kb-1#p0"
    with pytest.raises(ValueError):
        doc("bad", audience="everyone")


def test_personal_data_is_masked_before_it_is_embedded(ingestion):
    text = "Customer sarah@acme.com paid with 4111 1111 1111 1111."
    ingestion.run_batch("t_us", "tickets", [doc("tk-1", text, source="tickets", source_kind="ticket")])
    [payload] = payloads(ingestion, "t_us", "tk-1")
    assert "sarah@acme.com" not in payload["text"] + payload["parent_text"] and "<EMAIL>" in payload["text"]


def test_tenants_see_only_public_and_their_own_collection(ingestion):
    for scope, source, doc_id, text in (("public", "docs", "pub-1", "Public release notes for version two."),
                                        ("t_us", "wiki", "us-1", "Acme internal runbook for release two."),
                                        ("t_eu", "wiki", "eu-1", "Globex internal runbook for release two.")):
        ingestion.run_batch(scope, source, [doc(doc_id, text, source=source)])
    query = embed(["runbook for release two"])[0]
    seen = {tier_tenant: {h.payload["doc_id"] for h in ingestion.index.search(*tier_tenant, query, limit=50)}
            for tier_tenant in (("T0_ANON", "t_us"), ("T1_VERIFIED", "t_us"), ("T2_SSO", "t_eu"))}
    assert "us-1" not in seen[("T0_ANON", "t_us")] and "pub-1" in seen[("T0_ANON", "t_us")]
    assert {"pub-1", "us-1"} <= seen[("T1_VERIFIED", "t_us")] and "eu-1" not in seen[("T1_VERIFIED", "t_us")]
    assert "eu-1" in seen[("T2_SSO", "t_eu")] and "us-1" not in seen[("T2_SSO", "t_eu")]


def test_collection_names_come_only_from_valid_tenant_ids():
    assert collections_for("T0_ANON", "t_us") == ["public_knowledge_v1"]
    assert collection("t_US") == "tenant_t_us_private_v1"
    with pytest.raises(ValueError):
        collection("t_us/../public")


def test_copied_acls_filter_and_restricted_documents_are_never_decided_locally(ingestion):
    ingestion.run_batch("t_us", "drive", [
        doc("hr-1", "Payroll policy for release two.", source="drive", acl_groups=["hr"]),
        doc("legal-1", "Legal hold policy for release two.", source="drive", is_restricted=True),
        doc("all-1", "Office policy for release two.", source="drive")])
    query = embed(["policy for release two"])[0]

    def found(groups):
        hits = ingestion.index.search("T2_SSO", "t_us", query, groups, limit=50)
        return {h.payload["doc_id"] for h in hits}

    assert "hr-1" in found(["hr"]) and "hr-1" not in found(["sales"])
    assert "legal-1" not in found(["hr", "legal"]) and "all-1" in found([])


def test_a_purged_document_vanishes_at_once_and_the_batch_does_not_bring_it_back(ingestion):
    ingestion.run_batch("t_us", "crm", [doc("crm-1", source="crm"), doc("crm-2", source="crm")])
    ingestion.purge("t_us", "crm-1", "erasure request")
    assert ingestion.index.count("t_us", "crm-1") == 0
    report = ingestion.run_batch("t_us", "crm", [doc("crm-1", source="crm"), doc("crm-2", source="crm")])
    assert report["skipped_tombstoned"] == 1 and ingestion.index.count("t_us", "crm-1") == 0
    assert ingestion.index.count("t_us", "crm-2") > 0


def test_the_batch_reindexes_in_place_and_purges_documents_the_source_dropped(ingestion):
    long_text = " ".join(f"line {i} of the old guide." for i in range(120))
    ingestion.run_batch("public", "guides", [doc("g-1", long_text, source="guides"),
                                             doc("g-2", source="guides")])
    assert ingestion.index.count("public", "g-1") > 1
    report = ingestion.run_batch("public", "guides", [doc("g-1", "Short new guide.", source="guides")])
    assert ingestion.index.count("public", "g-1") == 1
    assert [p["text"] for p in payloads(ingestion, "public", "g-1")] == ["Short new guide."]
    assert report["purged_missing"] == ["g-2"] and ingestion.index.count("public", "g-2") == 0
    assert "g-2" in ingestion.tombstones("public")


def test_the_post_batch_audit_removes_a_tombstoned_document_that_slipped_through(ingestion):
    ingestion.run_batch("t_eu", "chat", [doc("chat-1", source="chat", source_kind="ticket")])
    ingestion.conn.execute("INSERT INTO knowledge_tombstones (scope, doc_id, reason) "
                           "VALUES ('t_eu', 'chat-1', 'purge listener missed it')")
    report = ingestion.run_batch("t_eu", "other", [])
    assert report["leaked_and_repurged"] == ["chat-1"] and ingestion.index.count("t_eu", "chat-1") == 0


def test_each_batch_keeps_a_versioned_raw_snapshot(ingestion):
    ingestion.run_batch("public", "notes", [doc("n-1", source="notes")], batch_date=date(2026, 10, 16))
    assert latest_snapshot(ingestion.snapshots, "public", date(2026, 10, 16)).endswith("raw_v1.tar.gz")


def test_hidden_documents_do_not_use_up_result_slots(ingestion):
    ingestion.run_batch("t_us", "vault", [
        doc("secret-1", "Quarterly audit findings summary.", source="vault", is_restricted=True),
        doc("open-1", "Quarterly audit checklist.", source="vault")])
    query = embed(["Quarterly audit findings summary."])[0]  # the restricted document is the best match
    hits = ingestion.index.search("T2_SSO", "t_us", query, limit=1)
    assert [h.payload["doc_id"] for h in hits] == ["open-1"]


def test_ensure_indexes_every_filtered_field_on_new_and_existing_collections(stack):
    """B18: Qdrant Cloud refuses a filtered search on an unindexed field (the local Qdrant does not, so the
    index list is checked directly). A collection from before the fix (doc_id, source_system) is repaired."""
    client, namespace = stack
    index = Index(client, DIMENSIONS, namespace)
    old = collection("t_old", namespace)
    client.create_collection(old, vectors_config={"dense": models.VectorParams(
        size=DIMENSIONS, distance=models.Distance.COSINE)})
    for field in ("doc_id", "source_system"):
        client.create_payload_index(old, field, models.PayloadSchemaType.KEYWORD, wait=True)
    for scope in ("t_old", "t_new"):
        schema = client.get_collection(index.ensure(scope)).payload_schema
        assert {field: info.data_type for field, info in schema.items()} == PAYLOAD_INDEXES
