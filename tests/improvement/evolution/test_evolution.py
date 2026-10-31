"""M6-03: the monthly user-distribution check, the quarterly owned-risk review over the open-issues register,
and the retraining corpus that leaves out every erased user, with checkpoint retirement (NFR-35). No model is
trained (no GPU in v1)."""

import json
import math
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

from channels import sessions
from channels.api import turns
from channels.identity import otp
from data import events
from data.erasure import subject_hash
from data.migrate import migrate, psql
from evals import datasets
from improvement.evolution import (
    DRIFT_AT,
    Checkpoints,
    Example,
    Signal,
    build_corpus,
    current_register,
    drift_report,
    erased_subjects,
    kl_divergence,
    opted_in_samples,
    owned_risk_review,
    register_items,
    write_corpus,
)
from memory.conversation import dialogue
from memory.facts import store as fact_store
from memory.lifecycle import inventory, retention
from safety.governance import records

ROOT = Path(__file__).resolve().parents[3]
DB = "test_evolution"
DSN = f"postgresql://agent:change-me@127.0.0.1:5432/{DB}"  # credentials from .env.example
NOW = datetime(2026, 12, 31, tzinfo=timezone.utc)
PRIORS = {"BILLING_INQUIRY": 0.3, "TECHNICAL_OUTAGE": 0.3, "PASSWORD_RESET": 0.2, "GENERAL_INQUIRY": 0.2}


# The monthly distribution check -----------------------------------------------------------------------------

def test_traffic_that_matches_the_priors_keeps_the_evaluation_weights():
    report = drift_report({"BILLING_INQUIRY": 300, "TECHNICAL_OUTAGE": 300, "PASSWORD_RESET": 200,
                           "GENERAL_INQUIRY": 200}, PRIORS)
    assert report.kl < 1e-6 and not report.drifted and report.weights == report.assumed


def test_shifted_traffic_is_drift_and_recalibrates_the_weights_to_what_customers_ask():
    report = drift_report({"BILLING_INQUIRY": 50, "TECHNICAL_OUTAGE": 850, "PASSWORD_RESET": 50,
                           "SUBSCRIPTION_UPDATE": 50}, PRIORS)
    assert report.drifted and report.kl >= DRIFT_AT and report.weights == report.observed
    assert sum(report.weights.values()) == pytest.approx(1.0) and min(report.weights.values()) > 0
    assert report.weights["TECHNICAL_OUTAGE"] == pytest.approx(0.85, abs=1e-3)
    assert report.unseen == ("SUBSCRIPTION_UPDATE",)          # needs new episodes, not just a weight
    with pytest.raises(ValueError, match="no traffic"):
        drift_report({}, PRIORS)


def test_the_divergence_is_kl_of_observed_against_assumed():
    p, q = {"a": 0.9, "b": 0.1}, {"a": 0.5, "b": 0.5}
    assert kl_divergence(p, q) == pytest.approx(0.9 * math.log(1.8) + 0.1 * math.log(0.2))


# The quarterly owned-risk review ----------------------------------------------------------------------------

def test_the_living_risk_log_is_the_current_open_issues_register_with_every_item():
    items = register_items(current_register(ROOT / ".genesis" / "project.json"))
    for kind in "SBWL":
        numbers = sorted(int(i.id[1:]) for i in items if i.id[0] == kind)
        assert numbers == list(range(1, max(numbers) + 1)), kind          # none missed, none invented
    assert len(items) >= 100 and all(i.text for i in items)


def test_items_are_read_from_their_own_entries_not_from_references_inside_others():
    text = ("SECURITY: S1 the vault is open (see W2 and L3). S2 'quoted' risk.\nBUGS: B1 a crash, worse than "
            "L3 says. NOT YET B2 flaky.\nWIRED: W1 nothing wired; W2 not on a schedule.\nACCEPTED LIMITS / "
            "DESIGN DEBT: L1 one VM.")
    items = register_items(text)
    assert [(i.id, i.kind) for i in items] == [("S1", "security"), ("S2", "security"), ("B1", "bug"),
                                               ("B2", "bug"), ("W1", "wiring"), ("W2", "wiring"),
                                               ("L1", "accepted limit")]
    assert items[0].text == "the vault is open (see W2 and L3)."
    assert items[3].text == "flaky."


def test_risks_that_rose_become_epics_ordered_by_severity_and_unmeasured_ones_are_listed():
    items = register_items("S1 a. S2 b. B1 c. W1 d. L1 e.")
    review = owned_risk_review(items, {"S1": Signal(2, 2, 4, 4), "S2": Signal(0, 3, 2, 4),
                                       "B1": Signal(5, 9, 2, 2), "W1": Signal(4, 1, 2, 2)})
    assert [r.id for r in review.epics] == ["S2", "B1"]
    assert review.steady == ("S1", "W1") and review.unmeasured == ("L1",)


# The retraining corpus --------------------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def database():
    subprocess.run(["docker", "compose", "-f", "deploy/compose.yaml", "--env-file", ".env.example",
                    "up", "-d", "--wait", "postgres"], cwd=ROOT, check=True, capture_output=True)
    psql("postgres", f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)')
    psql("postgres", f'CREATE DATABASE "{DB}"')
    migrate("main", DB)
    for module in (sessions, turns, otp, events, dialogue, fact_store, records, inventory, retention,
                   datasets):
        module.apply_schema(DB)                  # the erasure inventory's order (tests/erasure)
    psql(DB, "INSERT INTO tenants (id, name, region) VALUES ('t_us', 'Acme', 'US'), ('t_eu', 'Globex', 'EU')")
    rows = [("t_us", "US", "sarah", ["My invoice <EMAIL_1a2b3c4d> is wrong"]),
            ("t_us", "US", "erin", ["Please refund order 7"]),
            ("t_us", "US", "ivan", ["Reset my password"]),
            ("t_us", "US", "omar", ["Call me at 415-555-0100 about the outage"]),
            ("t_eu", "EU", "hans", ["Mein VPN geht nicht"])]
    for tenant, region, user, said in rows:
        psql(DB, "INSERT INTO eval_samples (tenant_id, region, user_hash, conversation_id, stratum, route, "
                 f"turns) VALUES ('{tenant}', '{region}', '{subject_hash(tenant, user)}', 'c-{user}', "
                 f"'routine', 'faq', '{json.dumps(said)}'::jsonb)")
    for job, user, status in (("j1", "erin", "COMPLETED"), ("j2", "ivan", "IN_PROGRESS")):
        psql(DB, "INSERT INTO erasure_ledger (job_id, tenant_id, subject_hash, requested_by, reason, status) "
                 f"VALUES ('{job}', 't_us', '{subject_hash('t_us', user)}', 'dpo', 'article 17', '{status}')")


def connect():
    return psycopg.connect(DSN, autocommit=True)


def test_the_corpus_leaves_out_every_erased_user_other_regions_and_untokenized_text():
    examples = opted_in_samples(connect, ["t_us", "t_eu"])
    erased = erased_subjects(connect, ["t_us", "t_eu"])
    assert set(erased) == {subject_hash("t_us", "erin"), subject_hash("t_us", "ivan")}   # in progress counts
    corpus = build_corpus(examples, erased, "US", now=NOW)
    assert [e.turns for e in corpus.examples] == [("My invoice <EMAIL_1a2b3c4d> is wrong",)]
    assert corpus.excluded == {"erased_user": 2, "not_tokenized": 1, "other_region": 1}
    assert corpus.members == (subject_hash("t_us", "sarah"),)


def test_the_written_corpus_holds_no_erased_users_text_and_its_manifest_traces_members(tmp_path):
    examples, erased = opted_in_samples(connect, ["t_us"]), erased_subjects(connect, ["t_us"])
    corpus = build_corpus(examples, erased, "US", now=NOW)
    folder = write_corpus(corpus, tmp_path)
    text = (folder / "corpus.jsonl").read_text(encoding="utf-8")
    assert "refund order" not in text and "Reset my password" not in text and "415-555" not in text
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["content_hash"] == corpus.content_hash and folder.name.endswith(corpus.content_hash[:16])
    assert manifest["members"] == [subject_hash("t_us", "sarah")] and manifest["region"] == "US"
    with pytest.raises(FileExistsError):
        write_corpus(corpus, tmp_path)                            # a built corpus is never overwritten


def test_the_same_inputs_build_the_same_corpus():
    examples = opted_in_samples(connect, ["t_us"])
    erased = erased_subjects(connect, ["t_us"])
    first, second = build_corpus(examples, erased, "US"), build_corpus(examples, erased, "US")
    assert first.content_hash == second.content_hash


# Checkpoints ------------------------------------------------------------------------------------------------

def manifest(region, *users):
    return {"region": region, "content_hash": "h", "members": list(users)}


def test_promotion_needs_the_gate_and_retires_every_earlier_checkpoint_of_the_region(tmp_path):
    record = Checkpoints(tmp_path / "checkpoints.json")
    for checkpoint_id, region in (("us-q3", "US"), ("eu-q3", "EU"), ("us-q4", "US")):
        record.register(checkpoint_id, manifest(region, "u1"))
    with pytest.raises(ValueError, match="already registered"):
        record.register("us-q3", manifest("US"))
    assert record.promote("us-q3", gate_passed=True) == []
    with pytest.raises(ValueError, match="release gate"):
        record.promote("us-q4", gate_passed=False)
    assert record.promote("us-q4", gate_passed=True) == ["us-q3"]          # expunge these weights
    status = {c.id: c.status for c in record.all()}
    assert status == {"us-q3": "retired", "eu-q3": "candidate", "us-q4": "live"}
    with pytest.raises(ValueError, match="not a candidate"):
        record.promote("us-q3", gate_passed=True)


def test_a_checkpoint_holding_a_user_erased_later_must_be_retrained_within_ninety_days(tmp_path):
    record = Checkpoints(tmp_path / "checkpoints.json")
    record.register("us-old", manifest("US", "u-erin"))
    record.register("us-q3", manifest("US", "u-sarah", "u-erin"))
    record.promote("us-old", gate_passed=True)
    record.promote("us-q3", gate_passed=True)                               # retires us-old
    erased = {"u-erin": datetime(2026, 10, 1, tzinfo=timezone.utc)}
    assert record.erasure_deadlines(erased, now=datetime(2026, 11, 1, tzinfo=timezone.utc)) == {
        "us-q3": (datetime(2026, 12, 30, tzinfo=timezone.utc), False)}
    assert record.erasure_deadlines(erased, now=NOW)["us-q3"][1] is True    # overdue
    assert record.erasure_deadlines({"u-nobody": NOW}) == {}


# One simulated month ----------------------------------------------------------------------------------------

def test_a_month_reports_drift_and_the_quarter_trains_only_on_users_who_did_not_ask_to_be_erased(tmp_path):
    drift = drift_report({"TECHNICAL_OUTAGE": 700, "BILLING_INQUIRY": 300}, PRIORS)
    tenants = ["t_us", "t_eu"]
    examples, erased = opted_in_samples(connect, tenants), erased_subjects(connect, tenants)
    corpus = build_corpus(examples, erased, "US", now=NOW)
    record = Checkpoints(tmp_path / "checkpoints.json")
    record.register("us-q4", corpus.manifest())
    assert drift.drifted and record.all()[0].corpus["members"] == [subject_hash("t_us", "sarah")]
    assert record.erasure_deadlines(erased_subjects(connect, ["t_us"])) == {}
    assert all(e.created_at <= datetime.now(timezone.utc) + timedelta(minutes=1) for e in corpus.examples)
    assert isinstance(corpus.examples[0], Example)
