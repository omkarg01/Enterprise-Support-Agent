"""M5-06: the VM's maintenance tools: the in-place re-index for a new embedding model (NFR-29 / DL-D11), the
month's spend against the cap (NFR-36, AC-15), the migration helpers, and the API entry point."""

import importlib.util
import json
import subprocess
import sys
import types
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient, models

from cost import budgets
from data.migrate import PsqlError, migrate, psql
from knowledge.index.store import Index, collection

ROOT = Path(__file__).resolve().parents[3]
DB = "test_vm_tools"
DSN = f"postgresql://agent:change-me@127.0.0.1:5432/{DB}"      # credentials from .env.example
LOCK = json.loads((ROOT / "deploy" / "vm" / "models.json").read_text(encoding="utf-8"))


def load(name):
    spec = importlib.util.spec_from_file_location(f"vm_{name}", ROOT / "deploy" / "vm" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reindex, spend, vm_migrate, serve = load("reindex"), load("spend"), load("migrate"), load("serve")


# The in-place re-index --------------------------------------------------------------------------------------

def chunk(doc, n, text):
    return {"chunk_id": f"{doc}-{n}", "doc_id": doc, "source_system": "kb", "text": text,
            "audience": "public"}


@pytest.fixture
def qdrant():
    client = QdrantClient(":memory:")
    index = Index(client, dimensions=3)
    faq = [chunk("faq-1", 0, "reset your password"), chunk("faq-1", 1, "restart the vpn client")]
    index.replace_document("public", "faq-1", faq, [[1, 0, 0], [0, 1, 0]])
    index.replace_document("t_us", "runbook-7", [chunk("runbook-7", 0, "rotate the db credentials")],
                           [[0, 0, 1]])
    client.create_collection("unrelated",
                             vectors_config={"v": models.VectorParams(size=2, distance="Cosine")})
    return client


def new_model(texts):
    return [[float(len(t)), 1.0, 0.0, 0.0, 0.0] for t in texts]


def test_every_knowledge_collection_is_rebuilt_with_the_new_dimensions_and_the_same_chunks(qdrant):
    before = {name: sorted(p.payload["chunk_id"] for p in qdrant.scroll(name, limit=100)[0])
              for name in (collection("public"), collection("t_us"))}
    assert reindex.reindex(qdrant, new_model, 5) == {collection("public"): 2, collection("t_us"): 1}
    for name, chunk_ids in before.items():
        info = qdrant.get_collection(name)
        assert info.config.params.vectors["dense"].size == 5
        points = qdrant.scroll(name, limit=100, with_vectors=True)[0]
        assert sorted(p.payload["chunk_id"] for p in points) == chunk_ids
        for point in points:                              # cosine vectors are stored normalised
            expected = new_model([point.payload["text"]])[0]
            norm = sum(x * x for x in expected) ** 0.5
            assert point.vector["dense"] == pytest.approx([x / norm for x in expected], rel=1e-5)
    assert qdrant.get_collection("unrelated").config.params.vectors["v"].size == 2


def test_a_model_that_fails_or_returns_the_wrong_size_leaves_the_index_as_it_was(qdrant):
    def broken(texts):
        raise ConnectionError("ollama is down")
    with pytest.raises(ConnectionError):
        reindex.reindex(qdrant, broken, 5)
    with pytest.raises(ValueError, match="wrong number or size"):
        reindex.reindex(qdrant, lambda texts: [[1.0, 2.0] for _ in texts], 5)
    assert qdrant.get_collection(collection("public")).config.params.vectors["dense"].size == 3
    assert qdrant.count(collection("public")).count == 2


@pytest.mark.parametrize("name, scope", [
    ("public_knowledge_v1", "public"), ("tenant_t_us_private_v1", "t_us"), ("x_public_knowledge_v1", None),
    ("unrelated", None), ("tenant_t_us_private_v0", None)])
def test_only_knowledge_collections_are_touched(name, scope):
    assert reindex.knowledge_scope(name) == scope
    assert reindex.knowledge_scope("ns_" + name, "ns_") == scope


# The month's spend ------------------------------------------------------------------------------------------

def test_the_ledger_is_priced_with_the_deployed_models_and_an_unpriced_model_is_reported():
    prices = spend.prices(LOCK)
    assert prices["claude-opus-5-5"] == (Decimal("4.0"), Decimal("20.0"))
    rows = [("claude-opus-5-5", 1_000_000, 100_000), ("llama-3.1-8b-instant", 9, 9), ("gpt-9", 5, 5)]
    total, unpriced = spend.month_spend(rows, prices)
    assert total == Decimal("6") and unpriced == ["gpt-9"]
    lines, over = spend.verdict(total, unpriced, 10)
    assert over and "gpt-9" in lines[1]
    assert spend.verdict(Decimal("9.99"), [], 10) == (["model spend this month: 9.9900 USD of 10 USD"], False)
    assert spend.verdict(Decimal("10"), [], 10)[1]


@pytest.fixture(scope="module")
def ledger_db():
    subprocess.run(["docker", "compose", "-f", "deploy/compose.yaml", "--env-file", ".env.example", "up",
                    "-d", "--wait", "postgres"], cwd=ROOT, check=True, capture_output=True)
    psql("postgres", f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)')
    psql("postgres", f'CREATE DATABASE "{DB}"')
    migrate("main", DB)
    budgets.apply_schema(DB)
    psql(DB, "INSERT INTO tenants (id, name, region) VALUES ('t_us', 'Acme', 'US'), ('t_eu', 'Globex', 'EU');"
             "INSERT INTO usage_ledger VALUES ('t_us', '2026-10', 'claude-opus-5-5', 'faq', 1000000, 100000),"
             "('t_eu', '2026-10', 'claude-opus-5-5', 'diagnostic', 500000, 50000),"
             "('t_eu', '2026-09', 'claude-opus-5-5', 'faq', 9000000, 0)")
    return DSN


def test_the_spend_check_sums_every_tenant_for_the_month(ledger_db, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", ledger_db)
    assert spend.main(["2026-10"]) == 0
    assert "9.0000 USD of 10 USD" in capsys.readouterr().out          # 6 + 3; September is not counted
    assert spend.main(["2026-09"]) == 1                                 # 36 USD: over the cap


# The migration helpers --------------------------------------------------------------------------------------

@pytest.mark.parametrize("url, host, database, sslmode", [
    ("postgresql://u:p%40ss@ep-cool-1-pooler.eu-central-1.aws.neon.tech/neondb?sslmode=require",
     "ep-cool-1.eu-central-1.aws.neon.tech", "neondb", "require"),
    ("postgres://agent:change-me@127.0.0.1:5432/agent", "127.0.0.1", "agent", "prefer"),
    ("postgresql://agent:x@host.docker.internal:5432/a?sslmode=disable", "host.docker.internal", "a",
     "disable"),
])
def test_psql_goes_around_the_pooler_to_the_direct_endpoint(url, host, database, sslmode):
    main_db, env = vm_migrate.psql_environment(url)
    assert (main_db, env["PGHOST"], env["PGSSLMODE"]) == (database, host, sslmode)
    assert url.startswith("postgres://") or env["PGPASSWORD"] in ("p@ss", "change-me", "x")


def test_every_module_owning_a_schema_is_migrated():
    names = vm_migrate.module_schemas()
    assert len(names) == len(set(names)) >= 17
    assert {"channels.api.turns", "hitl.queues", "cost.budgets", "evals.datasets",
            "tools.audit"} <= set(names)


def test_schemas_that_depend_on_each_other_are_applied_in_rounds():
    applied = []

    def module(name, needs=()):
        def apply_schema(database):
            if any(n not in applied for n in needs):
                raise PsqlError(f'relation "{needs[0]}" does not exist')
            applied.append(name)
        return types.SimpleNamespace(__name__=name, apply_schema=apply_schema)

    rounds = vm_migrate.apply_in_rounds([module("handoffs", ("queues",)), module("queues", ("tenants",)),
                                         module("tenants")], "db", PsqlError)
    assert rounds == [["tenants"], ["queues"], ["handoffs"]]
    with pytest.raises(SystemExit, match='orphan: relation "nowhere" does not exist'):
        vm_migrate.apply_in_rounds([module("orphan", ("nowhere",))], "db", PsqlError)


def test_the_role_bootstrap_is_valid_and_creates_every_role_once():
    sql = vm_migrate.BOOTSTRAP
    assert sql.count("DO $$") == 1 and sql.count("END $$;") == 1
    assert all(f"rolname = '{role}'" in sql for role in vm_migrate.ROLES)


# The API entry point ----------------------------------------------------------------------------------------

def test_the_vm_api_has_no_mock_sso_route_and_answers_its_health_check(ledger_db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", ledger_db)
    api = serve.app({"APP_REVISION": "r1"})
    paths = set(api.openapi()["paths"])
    assert "/v1/identity/mock-idp" not in paths and {"/healthz", "/v1/identity/anonymous"} <= paths
    assert TestClient(api).get("/healthz").json() == {"status": "ok", "revision": "r1"}
    with pytest.raises(SystemExit, match="SUPPORT_AGENT_DEV_AUTH"):
        serve.app({"SUPPORT_AGENT_DEV_AUTH": "1"})


def test_the_vm_tools_import_nothing_the_image_lacks():
    """reindex, spend, migrate and serve run in the image: only its dependencies and the standard library."""
    import tomllib

    allowed = {"psycopg", "qdrant_client", "httpx", "uvicorn", "fastapi"} | set(sys.stdlib_module_names)
    packages = {"channels", "core", "cost", "data", "knowledge", "evals"}
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    assert {"qdrant-client>=1.12,<1.13", "httpx>=0.27", "uvicorn>=0.30", "fastapi>=0.115"} <= set(deps)
    import ast
    for name in ("reindex", "spend", "migrate", "serve"):
        tree = ast.parse((ROOT / "deploy" / "vm" / f"{name}.py").read_text(encoding="utf-8"))
        roots = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        roots |= {n.module.split(".")[0] for n in ast.walk(tree)
                  if isinstance(n, ast.ImportFrom) and n.module}
        assert roots <= allowed | packages, (name, roots - allowed - packages)
