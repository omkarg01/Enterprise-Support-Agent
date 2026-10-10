"""M0-03b: every library the plan names is declared in pyproject.toml and imports (NFR-26, NFR-36)."""

import importlib
import re
import tomllib
from pathlib import Path

import pytest

PYPROJECT = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
# Distribution name -> module it provides.
MODULES = {
    "psycopg": "psycopg", "cryptography": "cryptography", "langgraph": "langgraph.graph",
    "langgraph-checkpoint-postgres": "langgraph.checkpoint.postgres", "temporalio": "temporalio.workflow",
    "qdrant-client": "qdrant_client", "fastapi": "fastapi", "uvicorn": "uvicorn", "cedarpy": "cedarpy",
    "opentelemetry-sdk": "opentelemetry.sdk.trace", "opentelemetry-exporter-otlp":
    "opentelemetry.exporter.otlp.proto.grpc.trace_exporter", "httpx": "httpx", "anthropic": "anthropic",
    "boto3": "boto3", "pydantic": "pydantic", "streamlit": "streamlit", "locust": "locust",
    "pytest": "pytest", "ruff": "ruff",
}


def declared():
    project = PYPROJECT["project"]
    requirements = project["dependencies"] + sum(project["optional-dependencies"].values(), [])
    return [re.match(r"[A-Za-z0-9._-]+", r).group(0).lower() for r in requirements]


def test_every_declared_library_has_a_known_module():
    assert set(declared()) == set(MODULES)


@pytest.mark.parametrize("distribution", sorted(MODULES))
def test_library_imports(distribution):
    importlib.import_module(MODULES[distribution])
