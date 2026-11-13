"""M1-16: every automated decision is recorded, the customer's why view shows reasons and checks only, AI
disclosure and the human option ride on every reply, and transcripts are archived for 2 years (FR-34).
Runs against the compose Postgres."""

import json
import re
import subprocess
from pathlib import Path

import psycopg
import pytest

from data.migrate import PsqlError, migrate, psql
from safety.governance.archive import RawPII, TranscriptArchive
from safety.governance.disclosure import DISCLOSURE, TALK_TO_HUMAN, disclose
from safety.governance.records import (
    SCHEMA_NAME,
    DecisionLog,
    HumanReviewRequired,
    LeakyExplanation,
    apply_schema,
    require_sign_off,
)

ROOT = Path(__file__).resolve().parents[3]
DB = "test_governance"
DSN = f"postgresql://agent:change-me@127.0.0.1:5432/{DB}"  # credentials from .env.example
TRACE = {"jev": {"intent": "billing", "confidence": 0.93},
         "cedar": {"policy": "permit-read-own", "verdict": "allow"}, "hazard": {"llama_guard": 0.02},
         "models": {"answer": "openai/gpt-oss-20b"}, "prompt_version": "a1b2c3d"}
TRANSCRIPT = [{"role": "user", "content": "Refund INV-9821 to <EMAIL_1a2b3c4d> please"},
              {"role": "assistant", "content": "Done: the refund is on its way."}]


def connect():
    return psycopg.connect(DSN, autocommit=True)


@pytest.fixture(scope="module", autouse=True)
def database():
    subprocess.run(["docker", "compose", "-f", "deploy/compose.yaml", "--env-file", ".env.example",
                    "up", "-d", "--wait", "postgres"], cwd=ROOT, check=True, capture_output=True)
    psql("postgres", f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)')
    psql("postgres", f'CREATE DATABASE "{DB}"')
    migrate("main", DB)
    apply_schema(DB)
    psql(DB, "INSERT INTO tenants (id, name, region) VALUES ('t_us', 'Acme', 'US'), ('t_eu', 'Globex', 'EU')")


@pytest.fixture
def log():
    return DecisionLog(connect)


@pytest.fixture
def archive():
    return TranscriptArchive(connect)


def test_schema_is_applied_once_through_the_ledger():
    apply_schema(DB)
    assert psql(DB, f"SELECT count(*) FROM schema_migrations WHERE name = '{SCHEMA_NAME}'") == [["1"]]


def test_every_decision_is_recorded_and_the_why_view_shows_reasons_and_checks_only(log):
    log.record("t_us", "c1", "answer", "Your question matched our billing help articles.",
               ["Identity verification", "Content safety"], TRACE, turn_id="turn-1")
    log.record("t_us", "c1", "escalate", "A specialist will look at this with you.",
               ["Content safety"], TRACE)
    why = log.why("t_us", "c1")
    assert [entry["decision"] for entry in why] == ["answer", "escalate"]
    assert why[0] == {"decision": "answer", "reason": "Your question matched our billing help articles.",
                      "checks": ["Identity verification", "Content safety"], "requires_human_review": False,
                      "human_available": True}
    shown = json.dumps(why)
    assert not re.search(r"\d", shown)  # no scores, confidences, thresholds or ids
    secrets = ("permit-read-own", "llama_guard", "gpt-oss", "a1b2c3d")
    assert all(secret not in shown for secret in secrets)


def test_the_specialist_view_has_the_full_trace(log):
    log.record("t_us", "c2", "answer", "Matched our help articles.", ["Content safety"], TRACE, turn_id="t9")
    (row,) = log.audit("t_us", "c2")
    assert row["trace"] == TRACE and row["turn_id"] == "t9" and row["tenant_id"] == "t_us"


@pytest.mark.parametrize("reason, checks", [
    ("Confidence was high enough.", ["Identity verification"]),
    ("Refunds over $1,000 need approval.", []),
    ("Matched our help articles.", ["Hazard score below threshold"]),
    ("Matched our help articles.", ['permit (principal, action, resource)']),
    ("Your prompt asked for it.", []),
])
def test_reasons_that_would_leak_internals_are_refused_before_the_write(log, reason, checks):
    with pytest.raises(LeakyExplanation):
        log.record("t_us", "c3", "answer", reason, checks, TRACE)
    assert log.audit("t_us", "c3") == []


def test_tenants_see_only_their_own_decisions(log):
    log.record("t_us", "shared-id", "answer", "Matched our help articles.", [], {})
    log.record("t_eu", "shared-id", "escalate", "A specialist will help.", [], {})
    assert [entry["decision"] for entry in log.why("t_us", "shared-id")] == ["answer"]
    assert [entry["decision"] for entry in log.why("t_eu", "shared-id")] == ["escalate"]


def test_significant_decisions_need_a_human_sign_off(log):
    with pytest.raises(HumanReviewRequired):
        require_sign_off("refund_denial")
    require_sign_off("refund_denial", signed_by="specialist-7")
    require_sign_off("answer")
    log.record("t_us", "c4", "refund_denial", "The charge matches the signed order.", ["Order check"], {})
    log.record("t_us", "c4", "refund_denial", "The charge matches the signed order.", ["Order check"], {},
               signed_by="specialist-7")
    assert [entry["requires_human_review"] for entry in log.why("t_us", "c4")] == [True, False]
    assert log.audit("t_us", "c4")[1]["trace"] == {"signed_by": "specialist-7"}


def test_every_reply_carries_the_disclosure_and_the_human_option():
    reply = disclose({"text": "Hi", "actions": [{"id": "rate", "label": "Rate"}, TALK_TO_HUMAN]})
    assert reply["ai_generated"] is True and reply["disclosure"] == DISCLOSURE
    # the human option appears once, after the other actions
    assert reply["actions"] == [{"id": "rate", "label": "Rate"}, TALK_TO_HUMAN]
    assert disclose({"text": "Hi"})["actions"] == [TALK_TO_HUMAN]
    banner = (ROOT / "web" / "components" / "portal.tsx").read_text(encoding="utf-8")
    assert all(sentence in banner for sentence in DISCLOSURE.split(". "))  # same words as the portal's banner


def test_transcripts_are_archived_for_two_years_out_of_the_runtimes_reach(archive):
    archive.archive("t_us", "c5", TRANSCRIPT)
    ((transcript, archived_at, retain_until),) = archive.read("t_us", "c5")
    assert transcript == TRANSCRIPT and retain_until.year - archived_at.year == 2
    assert archive.read("t_eu", "c5") == []
    with connect() as conn, conn.transaction():
        conn.execute("SET LOCAL ROLE app_runtime")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM transcript_legal_archive")


def test_a_transcript_with_raw_pii_is_refused_without_echoing_it(archive):
    with pytest.raises(RawPII) as refused:
        archive.archive("t_us", "c6", [{"role": "user", "content": "I'm sarah@acme.com"}])
    assert "sarah" not in str(refused.value) and "EMAIL" in str(refused.value)
    assert archive.read("t_us", "c6") == []


def test_the_sweeper_deletes_only_expired_transcripts(archive):
    archive.archive("t_us", "c7", TRANSCRIPT)
    psql(DB, "INSERT INTO transcript_legal_archive "
             "(tenant_id, conversation_id, transcript, archived_at, retain_until) "
             "VALUES ('t_us', 'c8', '[]', now() - interval '2 years 1 day', now() - interval '1 day')")
    assert archive.sweep("t_us") == 1
    assert archive.read("t_us", "c8") == [] and len(archive.read("t_us", "c7")) == 1
    with pytest.raises(PsqlError, match="retained until"):
        psql(DB, "DELETE FROM transcript_legal_archive WHERE conversation_id = 'c7'")


def test_a_shorter_retention_is_refused():
    with pytest.raises(PsqlError, match="retained_two_years"):
        psql(DB, "INSERT INTO transcript_legal_archive "
                 "(tenant_id, conversation_id, transcript, retain_until) "
                 "VALUES ('t_us', 'c9', '[]', now() + interval '30 days')")
