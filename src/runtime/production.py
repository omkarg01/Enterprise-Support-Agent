"""The turn loop's services from the environment and the model lock (deploy/vm/models.json), and the API app
that runs it: used by deploy/vm/serve.py on the VM and by `python -m runtime serve` on a laptop.

- Models: tier 0 Groq, tier 1 Gemini (both free, OpenAI-compatible), tier 2 Claude under the console spend
  limit; a tier whose key is not set is left out, and with no key at all every answer degrades to a hand-off.
- Jev when TYPESAFE_API_KEY is set (the pinned version from the lock, else its latest alias); otherwise NoJev,
  and triage uses the LLM classifier.
- Retrieval: Qdrant (QDRANT_URL, e.g. Qdrant Cloud; the compose service QDRANT_SERVICE_URL when it is unset or
  a loopback address) with the pinned Ollama embedder (OLLAMA_URL); query rewrite and reranking on
  the small model. Screening: Llama Guard on Ollama plus the injection patterns (an unavailable scorer gives
  the review band: reads only). PII tokens with PII_TOKEN_KEY (environment, then OpenBao). The statechart's
  checkpointer holds session state, so it uses the database's direct endpoint through a checked pool; the
  event log and the rest use DATABASE_URL.
"""

import json
import os
from pathlib import Path

import httpx
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from qdrant_client import QdrantClient

from channels.api.app import database_url
from channels.identity.server import build_app
from channels.identity.service import Identity
from core.orchestrator.decisions import DecisionEngine, JevClient
from core.secrets import load_secret
from cost.router import Router
from knowledge.retrieval.pipeline import Retriever
from reliability.fallbacks import (
    AllProvidersFailed,
    ProviderError,
    RetryBudget,
    call_chain,
    claude,
    openai_compatible,
)
from runtime import Dispatcher, NoJev, Services, TurnLoop, connector
from safety.governance.records import DecisionLog
from safety.pii.tokens import Tokenizer
from safety.screening.pipeline import Screener
from safety.screening.scorers import llama_guard, pattern_injection

ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / "deploy" / "vm" / "models.json"
ENDPOINTS = {"groq": "https://api.groq.com/openai/v1",
             "gemini": "https://generativelanguage.googleapis.com/v1beta/openai"}
KEYS = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}


def tiers(lock, environ):
    """Three provider lists, cheapest first, from the lock's tiers and the keys that are set."""
    out = []
    for _, spec in sorted(lock["tiers"].items()):
        key = environ.get(KEYS[spec["provider"]])
        if not key:
            out.append([])
        elif spec["provider"] == "anthropic":
            out.append([claude(api_key=key, model=spec["model"], usd_per_mtok=tuple(spec["usd_per_mtok"]))])
        else:
            out.append([openai_compatible(spec["provider"], ENDPOINTS[spec["provider"]], spec["model"], key)])
    return out


def small_model(provider_tiers, max_tokens=400):
    """prompt -> text on the free tiers. A provider outage raises ValueError, which the triage fallback and
    the query rewrite both read as "no usable answer" (so triage asks the customer: register B10)."""
    free = [p for tier in provider_tiers[:2] for p in tier]

    def complete(prompt):
        try:
            return call_chain(free, [{"role": "user", "content": prompt}], max_tokens, RetryBudget(),
                              {"retries": 0}).text
        except (AllProvidersFailed, ProviderError) as error:
            raise ValueError(f"no small model answered: {error}") from error
    return complete


def ollama_embed(base_url, model, timeout=60.0):
    http = httpx.Client(base_url=base_url, timeout=timeout)

    def embed(texts):
        reply = http.post("/api/embed", json={"model": model, "input": list(texts)})
        reply.raise_for_status()
        return reply.json()["embeddings"]
    return embed


def qdrant_url(environ):
    """QDRANT_URL as configured (Qdrant Cloud or another host); unset or loopback (meaningless inside a
    container) means the compose Qdrant, QDRANT_SERVICE_URL."""
    url = environ.get("QDRANT_URL") or ""
    host = url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    if not url or host in ("127.0.0.1", "localhost"):
        return environ.get("QDRANT_SERVICE_URL") or url or "http://127.0.0.1:6333"
    return url


def direct(dsn):
    """Neon's pooled endpoint is a transaction pooler: session state (the checkpointer) goes around it."""
    return dsn.replace("-pooler.", ".", 1)


def checkpointer(dsn, max_size=4):
    """The statechart's Postgres checkpointer for a long-lived process: a small pool that checks each
    connection before use (Neon closes idle connections when its compute suspends), shared by the worker
    threads."""
    settings = {"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row}
    pool = ConnectionPool(dsn, min_size=1, max_size=max_size, check=ConnectionPool.check_connection,
                          open=True, kwargs=settings)
    saver = PostgresSaver(pool)
    saver.setup()
    return saver


def services(environ, checkpointer, lock=None):
    lock = lock or json.loads(Path(environ.get("MODELS_FILE", MODELS)).read_text(encoding="utf-8"))
    provider_tiers = tiers(lock, environ)
    complete = small_model(provider_tiers)
    jev_key = environ.get("TYPESAFE_API_KEY")
    pinned = (lock.get("jev") or {}).get("model") or "jev-latest"
    jev = JevClient(jev_key, model=pinned) if jev_key else NoJev()
    ollama = environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
    qdrant = QdrantClient(url=qdrant_url(environ),
                          api_key=environ.get("QDRANT_API_KEY") or None)
    connect = connector(database_url())
    return Services(
        engine=DecisionEngine(jev), router=Router(provider_tiers),
        retriever=Retriever(qdrant, ollama_embed(ollama, lock["embedding"]["model"]), complete),
        screener=Screener(llama_guard(ollama, lock["screening"]["model"]), pattern_injection),
        tokens=Tokenizer(load_secret("PII_TOKEN_KEY")), complete=complete, connect=connect,
        checkpointer=checkpointer, decisions=DecisionLog(connect))


def log_code(tenant_id, user_id, code):
    print(f"[one-time code] {tenant_id}/{user_id}: {code}", flush=True)


def app(environ=os.environ, workers=4):
    """The API with sign-in (anonymous and one-time code; no mock SSO, no header sign-in) and the turn
    loop."""
    if environ.get("SUPPORT_AGENT_DEV_AUTH") == "1":
        raise SystemExit("refusing to serve with SUPPORT_AGENT_DEV_AUTH=1: anyone could sign in with a "
                         "header")
    loop = TurnLoop(services(environ, checkpointer(direct(database_url()))))
    connect = connector(database_url())
    api = build_app(None, Identity(connect, idp=None, send_otp=log_code), dispatch=Dispatcher(loop, workers))
    api.state.turn_loop = loop

    @api.get("/healthz")
    def healthz():
        with connect() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok", "revision": environ.get("APP_REVISION", "unknown")}

    return api
