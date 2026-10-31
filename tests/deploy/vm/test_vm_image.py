"""M5-06: the app image and the VM's compose layer (AC-15, NFR-36). The image is built here as the VM builds
it, migrates an empty database from inside the container (as the rollout does against Neon), and serves the
API read-only (as compose runs it): sign-in, a turn, /healthz, and no mock SSO."""

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import pytest

from data.migrate import psql

ROOT = Path(__file__).resolve().parents[3]
IMAGE = "support-agent-app:test"
DB, VAULT, KEYS = "test_vm_image", "test_vm_image_vault", "test_vm_image_keys"
URL = f"postgresql://agent:change-me@host.docker.internal:5432/{DB}?sslmode=disable"   # .env.example's
DOCKERFILE = (ROOT / "deploy" / "vm" / "Dockerfile").read_text(encoding="utf-8")
IGNORE = (ROOT / "deploy" / "vm" / "Dockerfile.dockerignore").read_text(encoding="utf-8").splitlines()


def bash():
    """A real bash: on Windows, Git's (System32's bash.exe is the WSL launcher)."""
    for candidate in (r"C:\Program Files\Gitinash.exe", shutil.which("bash")):
        if candidate and Path(candidate).exists() and "system32" not in candidate.lower():
            return candidate
    pytest.fail("no bash to check the setup script with")


def docker(*args, timeout=900, check=True):
    done = subprocess.run(["docker", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)
    if check:
        assert done.returncode == 0, (args[:3], done.stderr[-3000:])
    return done


def container_env():
    return ["--add-host", "host.docker.internal:host-gateway", "-e", f"DATABASE_URL={URL}",
            "-e", f"VAULT_DATABASE={VAULT}", "-e", f"KEYS_DATABASE={KEYS}", "-e", "PII_TOKEN_KEY=image-test"]


@pytest.fixture(scope="module")
def image():
    docker("compose", "-f", "deploy/compose.yaml", "--env-file", ".env.example", "up", "-d", "--wait",
           "postgres", timeout=300)
    for name in (DB, VAULT, KEYS):
        psql("postgres", f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    psql("postgres", f'CREATE DATABASE "{DB}"')
    docker("build", "-f", "deploy/vm/Dockerfile", "-t", IMAGE, ".")
    return IMAGE


@pytest.fixture(scope="module")
def migrated(image):
    run = docker("run", "--rm", *container_env(), image, "python", "deploy/vm/migrate.py", timeout=600)
    return run.stdout


# The compose layer ----------------------------------------------------------------------------------------

def vm_config(tmp_path, **extra_env):
    env_file = tmp_path / "vm.env"
    env_file.write_text((ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
    done = subprocess.run(
        ["docker", "compose", "-f", "deploy/compose.yaml", "-f", "deploy/vm/compose.vm.yaml", "--env-file",
         str(env_file), "config", "--format", "json"], cwd=ROOT, capture_output=True, text=True,
        env={**os.environ, "APP_ENV_FILE": str(env_file), **extra_env})
    return done


def test_the_vm_runs_the_api_and_the_backbone_but_not_the_local_postgres(tmp_path):
    done = vm_config(tmp_path, APP_IMAGE="support-agent-app:abc123", APP_REVISION="abc123")
    assert done.returncode == 0, done.stderr
    services = json.loads(done.stdout)["services"]
    assert set(services) == {"api", "temporal", "qdrant", "jaeger", "openbao", "ollama"}
    api = services["api"]
    assert api["image"] == "support-agent-app:abc123" and api["read_only"] and api["cap_drop"] == ["ALL"]
    env = api["environment"]
    assert env["SUPPORT_AGENT_DEV_AUTH"] == "0" and env["APP_REVISION"] == "abc123"
    assert env["QDRANT_SERVICE_URL"] == "http://qdrant:6333" and env["BAO_ADDR"] == "http://openbao:8200"
    public = [(name, p) for name, s in services.items() for p in s.get("ports", [])
              if p.get("host_ip") != "127.0.0.1"]
    assert [(name, p["published"]) for name, p in public] == [("api", "8000")]


def test_compose_refuses_to_start_the_api_without_an_image(tmp_path):
    done = vm_config(tmp_path)
    assert done.returncode != 0 and "set APP_IMAGE" in done.stderr


def test_the_setup_script_is_valid_bash_for_oracle_linux_and_ends_in_a_rollout():
    script = ROOT / "deploy" / "vm" / "setup-ol9.sh"
    done = subprocess.run([bash(), "-n", str(script)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    text = script.read_text(encoding="utf-8")
    assert "dnf install" in text and "firewall-cmd" in text and "--add-port=8000/tcp" in text
    assert "chmod 600 .env" in text and text.rstrip().endswith('python3 deploy/vm/release.py rollout "$REF"')
    assert "setenforce" not in text                       # SELinux stays enforcing


# The image ------------------------------------------------------------------------------------------------

def test_the_dockerfile_pins_its_base_runs_unprivileged_and_copies_only_what_it_needs():
    assert re.search(r"^FROM python:3\.11\.\d+-slim-bookworm$", DOCKERFILE, re.M)
    assert re.search(r"^USER agent$", DOCKERFILE, re.M)
    assert 'CMD ["python", "deploy/vm/serve.py"]' in DOCKERFILE
    sources = [line.split()[1] for line in DOCKERFILE.splitlines() if line.startswith("COPY ")]
    assert "." not in sources
    assert IGNORE[[i for i, line in enumerate(IGNORE) if not line.startswith("#")][0]] == "**"
    for source in sources:
        assert f"!{source}" in IGNORE or f"!{source}/**" in IGNORE, source
    for secret in ("**/.env", "**/*.key", "**/*.pem"):
        assert secret in IGNORE


def test_the_image_holds_no_secrets_tests_or_history_and_runs_as_a_plain_user(image):
    script = "id -u; ls -A /app; find /app -name '.env*' -o -name '*.key' -o -name '*.pem' | head"
    listing = docker("run", "--rm", image, "sh", "-c", script).stdout
    lines = listing.split()
    assert lines[0] == "10001"
    assert set(lines[1:]) == {"pyproject.toml", "src", "evals", "migrations", "policies", "deploy"}


def test_the_image_migrates_an_empty_database_completely_from_inside_the_container(migrated):
    assert "module schemas checked in" in migrated
    tables = {row[0] for row in psql(DB, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
    assert {"tenants", "turns", "outbound_event_log", "sessions", "escalations", "handoffs", "user_facts",
            "usage_ledger", "answer_cache", "eval_samples", "approval_requests"} <= tables
    assert len(tables) >= 30
    assert psql(VAULT, "SELECT count(*) FROM pg_tables WHERE tablename = 'pii_tokens'") == [["1"]]
    assert psql(KEYS, "SELECT count(*) FROM pg_tables WHERE tablename = 'user_keys'") == [["1"]]
    unforced = psql(DB, "SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT c.relforcerowsecurity")
    assert {row[0] for row in unforced} <= {"schema_migrations", "knowledge_tombstones"}   # register S15


def test_migrating_again_changes_nothing(migrated, image):
    before = psql(DB, "SELECT count(*) FROM schema_migrations")
    again = docker("run", "--rm", *container_env(), image, "python", "deploy/vm/migrate.py", timeout=600)
    assert "applied" not in again.stdout and psql(DB, "SELECT count(*) FROM schema_migrations") == before


@pytest.fixture(scope="module")
def served(migrated, image):
    psql(DB, "INSERT INTO tenants (id, name, region) VALUES ('t_us', 'Acme', 'US') ON CONFLICT DO NOTHING")
    name = f"vm-api-{uuid.uuid4().hex[:8]}"
    docker("run", "-d", "--name", name, "--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL",
           "--security-opt", "no-new-privileges:true", "-p", "127.0.0.1:18080:8000", *container_env(),
           "-e", "APP_REVISION=test-rev", image)
    base = "http://127.0.0.1:18080"
    for _ in range(60):
        try:
            if httpx.get(f"{base}/healthz", timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    yield base
    docker("rm", "-f", name, check=False)


def test_the_served_api_is_healthy_and_reports_its_revision(served):
    assert httpx.get(f"{served}/healthz").json() == {"status": "ok", "revision": "test-rev"}


def test_the_served_api_has_no_mock_sso_and_no_header_sign_in(served):
    assert httpx.post(f"{served}/v1/identity/mock-idp", json={"tenant_id": "t_us", "user_id": "x",
                                                               "nonce": "n"}).status_code == 404
    headers = {"X-Tenant-ID": "t_us", "X-User-ID": "sarah", "Idempotency-Key": "k-1"}
    assert httpx.post(f"{served}/v1/conversations/c1/turns", json={"content": "hi"},
                      headers=headers).status_code == 401
    sso = httpx.post(f"{served}/v1/identity/sso", json={"tenant_id": "t_us", "id_token": "x", "nonce": "n"})
    assert sso.status_code == 401


def test_a_customer_signs_in_anonymously_and_posts_a_turn(served):
    token = httpx.post(f"{served}/v1/identity/anonymous", json={"tenant_id": "t_us"}).json()["access_token"]
    reply = httpx.post(f"{served}/v1/conversations/c-vm/turns", json={"content": "Is the API up?"},
                       headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "k-vm-1"})
    assert reply.status_code == 202 and reply.json()["status"] == "QUEUED"
    turn_id = reply.json()["turn_id"]
    kinds = []
    for _ in range(60):                 # no model key and no Ollama here: the turn still ends, in a hand-off
        kinds = [row[0] for row in psql(DB, "SELECT event_type FROM outbound_event_log WHERE conversation_id "
                                            f"= 'c-vm' AND data->>'turn_id' = '{turn_id}' ORDER BY id")]
        if kinds and kinds[-1] in ("final", "handoff", "error"):
            break
        time.sleep(1)
    assert kinds[0] == "status" and kinds[-1] == "handoff", kinds


def test_the_container_refuses_to_serve_with_header_sign_in_on(image):
    done = docker("run", "--rm", *container_env(), "-e", "SUPPORT_AGENT_DEV_AUTH=1", image, check=False,
                  timeout=120)
    assert done.returncode != 0 and "refusing to serve with SUPPORT_AGENT_DEV_AUTH=1" in done.stderr
