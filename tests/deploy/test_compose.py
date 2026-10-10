"""M0-02: the learning-profile stack validates, runs on ARM64 and uses no paid service (NFR-26, NFR-36)."""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Pinned images whose registry manifests list an arm64 build (checked with `docker manifest inspect`).
ARM64_IMAGES = {
    "postgres:16-alpine",
    "temporalio/temporal:1.9.1",
    "qdrant/qdrant:v1.12.4",
    "jaegertracing/all-in-one:1.62.0",
    "openbao/openbao:2.1.0",
    "ollama/ollama:0.5.4",
}
# Every service .env.example may point at: free tiers, plus Claude under its console spend limit.
ALLOWED_ENV = {
    "MONTHLY_SPEND_CAP_USD", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB", "OPENBAO_DEV_TOKEN",
    "DATABASE_URL", "QDRANT_URL", "QDRANT_API_KEY", "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN",
    "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST", "GROQ_API_KEY", "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY", "TYPESAFE_API_KEY",
}


def compose_config():
    result = subprocess.run(
        ["docker", "compose", "-f", "deploy/compose.yaml", "--env-file", ".env.example",
         "config", "--format", "json"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def env_example():
    pairs = (line.split("=", 1) for line in (ROOT / ".env.example").read_text().splitlines()
             if line.strip() and not line.startswith("#"))
    return {k: v for k, v in pairs}


def test_stack_has_the_backbone_services_on_pinned_arm64_images():
    services = compose_config()["services"]
    assert set(services) == {"postgres", "temporal", "qdrant", "jaeger", "openbao", "ollama"}
    assert {s["image"] for s in services.values()} == ARM64_IMAGES


def test_ports_bind_to_localhost_only():
    for name, service in compose_config()["services"].items():
        for port in service.get("ports", []):
            assert port.get("host_ip") == "127.0.0.1", name


def test_env_names_only_free_or_capped_services():
    env = env_example()
    assert set(env) <= ALLOWED_ENV
    assert env["MONTHLY_SPEND_CAP_USD"] == "10"
