"""M6-02: improvement levers, the few-shot quarantine (no curated example in any evaluation set), knowledge
articles the agent drafts and a person publishes, and what a fix needs to merge: its failure test first, the
release gate, a before / after drop and a read-only canary (NFR-33, NFR-34)."""

import ast
import json
import subprocess
from pathlib import Path

import psycopg
import pytest

from data.migrate import migrate, psql
from improvement.feedback import Cluster, Review, taxonomy
from improvement.levers import (
    Canary,
    ClusterRun,
    Contaminated,
    FewShotRegistry,
    Fix,
    Lever,
    articles,
    contamination,
    corpus_hashes,
    drop_p_value,
    fingerprint,
    ready_to_merge,
    regression_test_lookup,
    suggested_levers,
)
from improvement.levers.articles import Articles, Refused, gap_topics
from tools.validation.approvals import Approver

ROOT = Path(__file__).resolve().parents[3]
DB = "test_levers"
DSN = f"postgresql://agent:change-me@127.0.0.1:5432/{DB}"  # credentials from .env.example
READ_ONLY = {"faq", "status", "documentation"}


# Levers -----------------------------------------------------------------------------------------------------

def test_every_taxonomy_node_has_its_usual_levers_and_none_suggests_fine_tuning():
    for node in taxonomy.TREE:
        assert Lever.FINE_TUNE not in suggested_levers(node)
    assert suggested_levers("retrieval.knowledge_gap")[0] is Lever.KB_ARTICLE
    assert suggested_levers("tools.argument_error")[0] is Lever.TOOL_DESCRIPTION
    assert suggested_levers("infrastructure.system_down") == ()


# The few-shot quarantine ------------------------------------------------------------------------------------

def evaluation_texts(root=ROOT):
    """Every utterance in the repository's evaluation sets: the framework seeds, the JSON under evals/, and
    the string literals of the evaluation tests."""
    texts = []
    for path in (root / "evals").rglob("*.json"):
        def strings(value):
            if isinstance(value, str):
                texts.append(value)
            elif isinstance(value, dict):
                [strings(v) for v in value.values()]
            elif isinstance(value, list):
                [strings(v) for v in value]
        strings(json.loads(path.read_text(encoding="utf-8")))
    for path in (root / "tests" / "evals").rglob("*.py"):
        texts += [node.value for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                  if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) > 20]
    return texts


def test_no_curated_example_is_in_an_evaluation_set_and_the_registry_chain_holds():
    registry = FewShotRegistry()
    assert registry.verify() == []
    assert contamination(registry, evaluation_texts()) == []


def test_the_contamination_check_finds_an_example_copied_from_an_evaluation_set(tmp_path):
    seed = json.loads((ROOT / "evals" / "datasets" / "framework_seeds.json").read_text(encoding="utf-8"))[0]
    registry = FewShotRegistry(tmp_path / "few_shot.jsonl")
    registry.curate("tools.argument_error", ["Thanks, that fixed it."], "ana", evaluation_hashes=set())
    copied = seed["turns"][0].upper() + "!!"
    registry.curate("triage.misclassified_intent", [copied], "ana", evaluation_hashes=set())
    assert len(contamination(registry, evaluation_texts())) == 1     # case and punctuation do not hide it


def test_curation_refuses_an_example_already_in_an_evaluation_set_or_holding_personal_data(tmp_path):
    registry = FewShotRegistry(tmp_path / "few_shot.jsonl")
    with pytest.raises(Contaminated, match="already appear"):
        registry.curate("gate.hallucination", ["The invoice total looks wrong"], "ana",
                        corpus_hashes(["the invoice total looks WRONG."]))
    with pytest.raises(ValueError, match="personal data"):
        registry.curate("gate.hallucination", ["mail me at sarah@acme.com"], "ana", set())
    with pytest.raises(KeyError):
        registry.curate("made.up", ["fine"], "ana", set())
    assert registry.entries() == []


def test_the_registry_is_append_only_an_edit_a_removal_or_a_reorder_breaks_the_chain(tmp_path):
    path = tmp_path / "few_shot.jsonl"
    registry = FewShotRegistry(path)
    for n in range(3):
        registry.curate("experience.repetitive", [f"example {'abc'[n]} of a good reply"], "ana", set())
    assert registry.verify() == []
    lines = path.read_text(encoding="utf-8").splitlines()
    edited = json.loads(lines[1])
    edited["turns"] = ["something else entirely"]
    path.write_text("\n".join([lines[0], json.dumps(edited), lines[2]]) + "\n", encoding="utf-8")
    assert any("edited" in p for p in registry.verify())
    path.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")
    assert any("chain broken" in p for p in registry.verify())
    path.write_text("\n".join([lines[1], lines[0], lines[2]]) + "\n", encoding="utf-8")
    assert any("chain broken" in p for p in registry.verify())


def test_fingerprints_ignore_case_spacing_and_punctuation_only():
    assert fingerprint("Reset my VPN!") == fingerprint("  reset   my vpn ")
    assert fingerprint("Reset my VPN") != fingerprint("Reset my VPN token")


# What a fix needs to merge ----------------------------------------------------------------------------------

TEST_REF = ("tests/improvement/levers/test_levers.py::"
            "test_fingerprints_ignore_case_spacing_and_punctuation_only")
GOOD = Fix(cluster="retrieval.knowledge_gap", lever=Lever.KB_ARTICLE, author="ana", tests=(TEST_REF,),
           gate_passed=True, before=ClusterRun(40, 14), after=ClusterRun(40, 1),
           canary=Canary("faq", {"reask_rate": 0.12, "csat": 0.81, "p95_ms": 2100},
                         {"reask_rate": 0.08, "csat": 0.84, "p95_ms": 2150}), reviewer="ben")
LOOKUP = regression_test_lookup(ROOT)


def test_a_fix_with_its_test_the_gate_a_significant_drop_a_clean_canary_and_a_review_may_merge():
    assert ready_to_merge(GOOD, LOOKUP, READ_ONLY) == []


@pytest.mark.parametrize("change, problem", [
    ({"tests": ()}, "write it before the fix"),
    ({"tests": ("tests/improvement/levers/test_levers.py::test_nothing_here",)}, "does not exist"),
    ({"tests": ("episode:seed-99-none",)}, "does not exist"),
    ({"gate_passed": False, "gate_failures": ("p95 latency 2600 ms over 2500.0",)}, "p95 latency 2600"),
    ({"after": None}, "no before / after run"),
    ({"after": ClusterRun(40, 11)}, "did not drop significantly"),
    ({"canary": None}, "no read-only canary"),
    ({"canary": Canary("billing_adjustment", GOOD.canary.baseline, GOOD.canary.candidate)},
     "is not read-only"),
    ({"touches_write_tool": True}, "cannot be proven on a canary"),
    ({"canary": Canary("faq", GOOD.canary.baseline, {**GOOD.canary.candidate, "csat": 0.70})}, "CSAT fell"),
    ({"canary": Canary("faq", GOOD.canary.baseline, {**GOOD.canary.candidate, "p95_ms": 2400})},
     "p95 latency"),
    ({"canary": Canary("faq", GOOD.canary.baseline, GOOD.canary.candidate, burn_alerts=2)}, "burn-rate"),
    ({"reviewer": None}, "no code review"),
    ({"reviewer": "ana"}, "cannot review their own fix"),
    ({"lever": Lever.FINE_TUNE}, "fine-tuning is not run in v1"),
])
def test_what_a_fix_still_lacks_is_listed(change, problem):
    fix = Fix(**{**GOOD.__dict__, **change})
    problems = ready_to_merge(fix, LOOKUP, READ_ONLY)
    assert any(problem in p for p in problems), problems


def test_an_evaluation_episode_counts_as_the_failure_test():
    fix = Fix(**{**GOOD.__dict__, "tests": ("episode:seed-6-degraded",)})
    assert ready_to_merge(fix, LOOKUP, READ_ONLY) == []


def test_the_before_after_drop_is_a_one_sided_two_proportion_test():
    assert drop_p_value(ClusterRun(40, 14), ClusterRun(40, 1)) < 0.001
    assert drop_p_value(ClusterRun(40, 10), ClusterRun(40, 8)) > 0.05
    assert drop_p_value(ClusterRun(40, 8), ClusterRun(40, 10)) > 0.5               # it rose
    assert drop_p_value(ClusterRun(0, 0), ClusterRun(10, 0)) == 1.0
    assert drop_p_value(ClusterRun(10, 0), ClusterRun(10, 0)) == 1.0


# Knowledge articles -----------------------------------------------------------------------------------------

def test_only_knowledge_gap_clusters_with_volume_get_an_article():
    clusters = (Cluster("retrieval.knowledge_gap", 5, 10), Cluster("retrieval.stale_document", 2, 10),
                Cluster("gate.hallucination", 9, 10))
    review = Review(clusters=clusters, postmortems=(), high_severity=())
    assert gap_topics(review) == ["retrieval.knowledge_gap"]


@pytest.fixture(scope="module", autouse=True)
def database():
    subprocess.run(["docker", "compose", "-f", "deploy/compose.yaml", "--env-file", ".env.example",
                    "up", "-d", "--wait", "postgres"], cwd=ROOT, check=True, capture_output=True)
    psql("postgres", f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)')
    psql("postgres", f'CREATE DATABASE "{DB}"')
    migrate("main", DB)
    articles.apply_schema(DB)
    articles.apply_schema(DB)
    psql(DB, "INSERT INTO tenants (id, name, region) VALUES ('t_us', 'Acme', 'US'), ('t_eu', 'Globex', 'EU')")


STAFF = {"km-ana": ("t_us", {"knowledge_manager"}), "spec-ben": ("t_us", {"specialist"}),
         "agent": ("t_us", {"knowledge_manager"}), "km-eu": ("t_eu", {"knowledge_manager"})}
CASES = [("case-1", "VPN fails after the update: reinstalling the client fixed it"),
         ("case-2", "VPN error after the update, fixed by reinstalling the client")]


def desk(generate=None):
    def verify(credential):                     # stands in for hitl.approval.staff_verifier
        tenant, roles = STAFF[credential]
        return Approver(tenant, credential, frozenset(roles))
    return Articles(lambda: psycopg.connect(DSN, autocommit=True),
                    generate or (lambda prompt: ("VPN fails after an update", "Reinstall the VPN client.")),
                    verify)


def test_a_draft_is_internal_and_by_the_agent_until_a_knowledge_manager_publishes_it():
    articles_ = desk()
    draft_id = articles_.draft("t_us", "retrieval.knowledge_gap", CASES)
    (row,) = psql(DB, "SELECT audience, author, status, source_cases FROM kb_article_drafts "
                      f"WHERE id = '{draft_id}'")
    assert row == ["internal", "agent", "draft", "{case-1,case-2}"]
    document = articles_.publish("t_us", draft_id, "km-ana")
    assert (document.audience, document.provenance, document.title) == ("customer_visible", "agent",
                                                                        "VPN fails after an update")
    (row,) = psql(DB, f"SELECT audience, status, reviewed_by FROM kb_article_drafts WHERE id = '{draft_id}'")
    assert row == ["customer_visible", "published", "km-ana"]
    with pytest.raises(Refused, match="only a pending draft"):
        articles_.publish("t_us", draft_id, "km-ana")


@pytest.mark.parametrize("reviewer, problem", [
    ("spec-ben", "needs the knowledge_manager role"), ("agent", "must be a person"),
    ("km-eu", "must be a person of this tenant")])
def test_no_one_but_a_knowledge_manager_of_the_tenant_publishes_and_never_the_agent(reviewer, problem):
    articles_ = desk()
    draft_id = articles_.draft("t_us", "retrieval.knowledge_gap", CASES)
    with pytest.raises(Refused, match=problem):
        articles_.publish("t_us", draft_id, reviewer)
    assert [d[0] for d in articles_.drafts("t_us")].count(draft_id) == 1


def test_a_rejection_says_why_and_the_draft_stays_internal():
    articles_ = desk()
    draft_id = articles_.draft("t_us", "retrieval.knowledge_gap", CASES)
    with pytest.raises(ValueError, match="says why"):
        articles_.reject("t_us", draft_id, "km-ana", " ")
    articles_.reject("t_us", draft_id, "km-ana", "The client version is wrong")
    (row,) = psql(DB, f"SELECT audience, status, review_note FROM kb_article_drafts WHERE id = '{draft_id}'")
    assert row == ["internal", "rejected", "The client version is wrong"]


def test_personal_data_never_reaches_a_draft():
    with pytest.raises(ValueError, match="tokenized before drafting"):
        desk().draft("t_us", "retrieval.knowledge_gap", [("case-3", "sarah@acme.com could not log in")])
    with pytest.raises(ValueError, match="holds personal data"):
        desk(lambda prompt: ("Login", "Ask sarah@acme.com")).draft("t_us", "retrieval.knowledge_gap", CASES)


def test_a_drafts_text_cannot_be_changed_and_it_cannot_be_customer_visible_unpublished():
    draft_id = desk().draft("t_us", "retrieval.knowledge_gap", CASES)
    with psycopg.connect(DSN, autocommit=True) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        with conn.transaction():
            articles._context(conn, "t_us")
            conn.execute("UPDATE kb_article_drafts SET body = 'edited' WHERE id = %s", (draft_id,))
    with psycopg.connect(DSN, autocommit=True) as conn, pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            articles._context(conn, "t_us")
            conn.execute("UPDATE kb_article_drafts SET audience = 'customer_visible' WHERE id = %s",
                         (draft_id,))


def test_tenants_see_only_their_own_drafts():
    desk().draft("t_us", "retrieval.knowledge_gap", CASES)
    assert desk().drafts("t_eu") == []
