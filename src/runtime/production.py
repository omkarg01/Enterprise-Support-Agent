"""The turn loop's services from the environment and the model lock (deploy/vm/models.json), and the API app
that runs it: used by deploy/vm/serve.py on the VM and by `python -m runtime serve` on a laptop.

- Models: tier 0 Groq, tier 1 Gemini (both free, OpenAI-compatible), tier 2 Claude under the console spend
  limit; a tier whose key is not set is left out, and with no key at all every answer degrades to a hand-off.
- Jev when TYPESAFE_API_KEY is set (the pinned version from the lock, else its latest alias); otherwise NoJev,
  and triage uses the LLM classifier.
- Retrieval: Qdrant (QDRANT_URL, e.g. Qdrant Cloud; the compose service QDRANT_SERVICE_URL when it is unset or
  a loopback address) with the pinned Ollama embedder (OLLAMA_URL); query rewrite and reranking on
  the small model. Screening: Llama Guard on Ollama plus the injection patterns (an unavailable scorer gives
  the review band: reads only; a Llama Guard block on a customer message gets Jev's second opinion). PII
  tokens with PII_TOKEN_KEY (environment, then OpenBao). Everything uses APP_DATABASE_URL (app_login, no
  BYPASSRLS) when set; the statechart's case state store, saved after every stage, goes through a small
  checked pool on it (its role and tenant settings are transaction-local, so Neon's pooler is fine).
- Tools: the specialists' read-only tools.nimbus catalog through tools.execution.Executor, audited (S11),
  with on-behalf-of tokens from the customer's live session (AGENT-02).
- Thumbs on replies (improvement.feedback): comments masked with the turn loop's tokenizer and triaged by Jev
  (behind Remasked, as the turn loop asks it); a critical one (severity >= 0.80) has no pager yet, so it is
  logged and leads the staff console's Feedback page.
- Sign-in codes: emailed over SMTP with STARTTLS (SMTP_HOST, SMTP_PORT default 587, SMTP_USER, SMTP_PASSWORD
  from the environment then OpenBao, SMTP_FROM default SMTP_USER); without SMTP_HOST they go to the log.
"""

import json
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

import httpx
from psycopg_pool import ConnectionPool
from qdrant_client import QdrantClient

from channels.api.app import database_url
from channels.api.staff import staff_router
from channels.identity.server import build_app
from channels.identity.service import Identity
from core.orchestrator.case_state import PostgresStore
from core.orchestrator.decisions import DecisionEngine, JevClient
from core.secrets import load_secret
from cost.router import Router
from improvement.feedback import Feedback
from knowledge.retrieval.pipeline import Retriever
from reliability.fallbacks import (
    AllProvidersFailed,
    ProviderError,
    RetryBudget,
    call_chain,
    claude,
    openai_compatible,
)
from runtime import NoJev, Remasked, Services, TurnLoop, connector
from safety.governance.records import DecisionLog
from safety.pii.tokens import Tokenizer
from safety.screening.pipeline import Screener, jev_confirm
from safety.screening.scorers import llama_guard, pattern_injection
from tools import desk, nimbus
from tools import services as services_tools
from tools.audit import AuditLog
from tools.execution import Executor
from tools.registry import Registry
from workflows.dispatch import TemporalDispatcher

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


def store(dsn, max_size=4):
    """The statechart's case state store for a long-lived process: a small pool that checks each connection
    before use (Neon closes idle connections when its compute suspends), shared by the worker threads."""
    pool = ConnectionPool(dsn, min_size=1, max_size=max_size, check=ConnectionPool.check_connection,
                          open=True, kwargs={"autocommit": True, "prepare_threshold": 0})
    return PostgresStore(pool.connection)


def services(environ, store, lock=None):
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
    engine = DecisionEngine(jev)
    # B24: a Llama Guard block on a customer message gets Jev's second opinion (none without Jev)
    screener = Screener(llama_guard(ollama, lock["screening"]["model"]), pattern_injection,
                        confirm=jev_confirm(engine))
    identity = Identity(connect)  # sessions and on-behalf-of tokens; its key never leaves this process
    tools = nimbus.catalog(connect, identity)
    # ponytail: no key store; every catalog tool is argument-free, so an audit record has nothing to seal (a
    # tool with arguments fails closed, AUDIT_UNAVAILABLE, until data.keys.KeyStore is wired here)
    executor = Executor(tools, nimbus.SYSTEMS, screener, identity=identity, audit=AuditLog(connect, None))
    return Services(
        engine=engine, router=Router(provider_tiers),
        retriever=Retriever(qdrant, ollama_embed(ollama, lock["embedding"]["model"]), complete),
        screener=screener, tokens=Tokenizer(load_secret("PII_TOKEN_KEY")), complete=complete,
        connect=connect, store=store, decisions=DecisionLog(connect), executor=executor,
        tools=tools, desk=customer_actions(connect, identity, screener))


def customer_actions(connect, identity, screener):
    """The executor for what a customer confirms on a card (raise a ticket, carry out a service action): its
    own registry, so no specialist is ever offered these tools."""
    registry = services_tools.register(desk.register(Registry(), connect, identity), connect, identity)
    return Executor(registry.freeze(), {**desk.SYSTEMS, **services_tools.SYSTEMS}, screener,
                    identity=identity, audit=AuditLog(connect, None))


def log_code(tenant_id, user_id, code):
    print(f"[one-time code] {tenant_id}/{user_id}: {code}", flush=True)


def code_sender(environ):
    """Identity's send_otp: email the code when SMTP_HOST is set, else log it. A failed send raises, so the
    challenge rolls back and the portal asks the customer to try again."""
    host = environ.get("SMTP_HOST")
    if not host:
        return log_code
    user = environ["SMTP_USER"]

    def email_code(tenant_id, user_id, code):
        message = EmailMessage()
        message["From"], message["To"] = environ.get("SMTP_FROM") or user, user_id
        message["Subject"] = f"Your sign-in code: {code}"
        message.set_content(f"Your support sign-in code is {code}.\n\nIt works once and expires in 10 "
                            "minutes. If you did not ask for it, ignore this email.\n")
        # ponytail: one connection per code, no rate limit on who can be mailed (register); pool and limit
        # per address when sign-in traffic is real
        with smtplib.SMTP(host, int(environ.get("SMTP_PORT", "587")), timeout=15) as smtp:
            smtp.starttls()
            smtp.login(user, load_secret("SMTP_PASSWORD"))
            smtp.send_message(message)
    return email_code


def app(environ=os.environ):
    """The API with sign-in (anonymous and one-time code; no mock SSO, no header sign-in) and the turn loop,
    whose turns run on Temporal (TEMPORAL_ADDRESS) with the worker in this process."""

    if environ.get("SUPPORT_AGENT_DEV_AUTH") == "1":
        raise SystemExit("refusing to serve with SUPPORT_AGENT_DEV_AUTH=1: anyone could sign in with a "
                         "header")
    turn_services = services(environ, store(database_url()))
    loop = TurnLoop(turn_services)
    connect = connector(database_url())
    # ponytail: no pager; a critical comment is a log line until on-call paging exists
    feedback = Feedback(connect, turn_services.tokens, Remasked(turn_services.engine),
                        lambda tenant, event: print(f"[feedback: critical] {tenant}: {event}", flush=True))
    dispatch = TemporalDispatcher(environ.get("TEMPORAL_ADDRESS", "127.0.0.1:7233"), loop)
    # Neon's free compute only scales to zero after 5 idle minutes, so nothing may query it on a timer: each
    # stream polls every 0.5 s and ends after 5 min (a visible portal tab reconnects with Last-Event-ID, a
    # hidden one waits until it is shown).
    api = build_app(None, Identity(connect, idp=None, send_otp=code_sender(environ)), dispatch=dispatch,
                    poll_seconds=0.5, stream_seconds=300.0, feedback=feedback)
    api.state.turn_loop = loop
    api.include_router(staff_router(connect))  # the demo staff console: NO sign-in (register S20)

    @api.get("/healthz")
    def healthz():
        """The rollout's check (release.py): the database answers."""
        with connect() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok", "revision": environ.get("APP_REVISION", "unknown")}

    @api.get("/livez")
    def livez():
        """The container's 30 s health check: the process answers, without waking the database."""
        return {"status": "ok"}

    return api
