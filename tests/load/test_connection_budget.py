"""One API process stays within 12 database connections (docs/aiven.md: Aiven free, max_connections 20).

A throwaway Postgres runs with max_connections=20, migrated like deploy/vm/migrate.py does. The API (the real
create_app with its shared pool and LISTEN streams, the real Identity sign-in, the real staff router behind
its staff check) serves under uvicorn in this process. Temporal is the one stand-in: an executor with
workflows.activities.ACTIVITIES threads runs each accepted turn the way runtime.TurnLoop touches the
database (status events, case state saves, the final reply). Under ~50 concurrent signed-in customers, 5 open
chat streams, staff console polling and that burst of turns, pg_stat_activity never shows more than 12
connections for the database, every request succeeds, and every stream gets its reply from a notification
well before the 10 s fallback poll.
"""

import importlib.util
import os
import socket
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import psycopg
import pytest
import uvicorn

from channels.api import turns
from channels.api.app import create_app
from channels.api.staff import staff_router
from channels.identity.service import Identity
from core.orchestrator.case_state import PostgresStore
from data import pool as pools
from data.events import EventLog
from data.migrate import PsqlError, migrate, psql
from runtime import connector
from workflows.activities import ACTIVITIES

ROOT = Path(__file__).resolve().parents[2]
CONTAINER = "support-agent-budget-pg"
BUDGET = 12
FALLBACK_POLL = 10.0   # production's poll_seconds: a stream woken only by it would wait up to this long
LEAD = "lead@nimbus.example"
OTHERS = ("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
          "AND pid <> pg_backend_pid()")


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def vm_migrate():
    spec = importlib.util.spec_from_file_location("vm_migrate", ROOT / "deploy" / "vm" / "migrate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def dsn():
    port = free_port()
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--rm", "--name", CONTAINER, "-e", "POSTGRES_USER=agent",
                    "-e", "POSTGRES_PASSWORD=change-me", "-e", "POSTGRES_DB=agent",
                    "-p", f"127.0.0.1:{port}:5432", "postgres:18-alpine", "-c", "max_connections=20"],
                   check=True, capture_output=True)
    url = f"postgresql://agent:change-me@127.0.0.1:{port}/agent"
    previous = os.environ.get("PSQL")
    os.environ["PSQL"] = f"docker exec -i {CONTAINER} psql -U agent"
    try:
        pools.wait_for(url)   # the startup retry, against a server still initialising
        assert psql("agent", "SHOW max_connections") == [["20"]]
        migrate("main", "agent")
        steps = vm_migrate()
        modules = [importlib.import_module(name) for name in steps.module_schemas()]
        steps.apply_in_rounds(modules, "agent", PsqlError)
        psql("agent", "INSERT INTO tenants (id, name, region) VALUES ('t_us', 'Acme', 'US')")
        yield url
    finally:
        if previous is None:
            os.environ.pop("PSQL", None)
        else:
            os.environ["PSQL"] = previous
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)


class Turns:
    """The Temporal worker, stood in for: ACTIVITIES turns at once, each using the shared main pool and the
    small case-state pool the way runtime.TurnLoop does, with a little model time between steps."""

    def __init__(self, url):
        self.connect, self.store = connector(url), PostgresStore(pools.pool(url, "store").connection)
        self.workers = ThreadPoolExecutor(max_workers=ACTIVITIES)
        self.finals, self.failures, self.running = {}, [], []

    def dispatch(self, turn):
        self.running.append(self.workers.submit(self.run, turn))

    def publish(self, turn, kind, data):
        with self.connect() as conn:
            turns.publish(EventLog(conn), turn.tenant_id, turn.conversation_id, kind,
                          {"turn_id": turn.turn_id, **data})

    def run(self, turn):
        try:
            case = f"case-{turn.conversation_id}"
            version = self.store.save(turn.tenant_id, case, {"stage": "TRIAGE"}, 0)
            for text in ("Reading your question", "Checking the knowledge base"):
                self.publish(turn, "status", {"state": "progress", "text": text})
                time.sleep(0.1)
                version = self.store.save(turn.tenant_id, case, {"stage": text}, version)
            self.publish(turn, "final", {"text": "Here is what I found."})
            self.finals[turn.conversation_id] = time.monotonic()
        except Exception as error:  # noqa: BLE001 - reported by the test
            self.failures.append(repr(error))


class Peak(threading.Thread):
    """Samples the database's connections (all but its own) every 10 ms."""

    def __init__(self, url):
        super().__init__(daemon=True)
        self.url, self.peak, self.stopped = url, 0, threading.Event()

    def run(self):
        with psycopg.connect(self.url, autocommit=True) as conn:
            while not self.stopped.is_set():
                (now,) = conn.execute(OTHERS).fetchone()
                self.peak = max(self.peak, now)
                time.sleep(0.01)


@pytest.fixture(scope="module")
def api(dsn):
    codes = []
    identity = Identity(connector(dsn), send_otp=lambda tenant, user, code: codes.append(code))
    agent = Turns(dsn)
    app = create_app(dsn, authenticate=identity.authenticate, dispatch=agent.dispatch,
                     poll_seconds=FALLBACK_POLL, stream_seconds=None)
    app.include_router(staff_router(connector(dsn), identity.authenticate, tenants=("t_us",),
                                    staff=frozenset({LEAD})))
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)

    def signed_in(email):
        return identity.verify_otp("t_us", identity.start_otp("t_us", email), codes[-1])
    yield f"http://127.0.0.1:{port}", identity, signed_in, agent
    server.should_exit = True
    thread.join(10)
    agent.workers.shutdown(wait=False)


def stream(base, token, conversation, got):
    """Read the conversation's stream until its final reply; record when it arrived."""
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(base_url=base, timeout=httpx.Timeout(10.0, read=None)) as http, \
            http.stream("GET", f"/v1/conversations/{conversation}/events", headers=headers) as reply:
        got[conversation] = ("open", reply.status_code)
        for line in reply.iter_lines():
            if line == "event: final":
                got[conversation] = ("final", time.monotonic())
                return


def customer(base, token, conversation, signed_in_tier):
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(base_url=base, headers=headers, timeout=30.0) as http:
        statuses = [http.post(f"/v1/conversations/{conversation}/turns", json={"content": "Reset MFA?"},
                              headers={"Idempotency-Key": str(uuid.uuid4())}).status_code,
                    http.get("/v1/conversations").status_code]
        if signed_in_tier:
            statuses.append(http.get("/v1/services").status_code)
    return statuses


def staff_console(base, token, rounds=5):
    with httpx.Client(base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=30.0) as http:
        return [http.get(path).status_code for _ in range(rounds)
                for path in ("/v1/staff/overview", "/v1/staff/customers")]


def test_one_api_process_stays_within_twelve_connections(dsn, api):
    base, identity, signed_in, agent = api
    members = [(signed_in(f"member{i}@acme.test"), True) for i in range(10)]
    guests = [(identity.anonymous("t_us"), False) for _ in range(40)]
    staff = [signed_in(LEAD) for _ in range(3)]
    peak = Peak(dsn)
    peak.start()

    got, readers = {}, []
    watched = [(token, f"stream-{i}") for i, (token, _) in enumerate(members[:5])]
    for token, conversation in watched:
        readers.append(threading.Thread(target=stream, args=(base, token, conversation, got), daemon=True))
        readers[-1].start()
    while len(got) < len(watched):
        time.sleep(0.01)

    with ThreadPoolExecutor(max_workers=len(members) + len(guests) + len(staff)) as crowd:
        calls = [crowd.submit(customer, base, token, f"stream-{i}" if i < 5 else f"c-{i}", tier)
                 for i, (token, tier) in enumerate(members + guests)]
        calls += [crowd.submit(staff_console, base, token) for token in staff]
        statuses = [status for call in calls for status in call.result()]
    for reader in readers:
        reader.join(FALLBACK_POLL)
    for running in list(agent.running):
        running.result(timeout=60)
    peak.stopped.set()
    peak.join(5)

    assert agent.failures == []
    assert statuses and set(statuses) <= {200, 202}, sorted(set(statuses))
    assert all(got[c][0] == "final" for _, c in watched), got
    late = {c: round(got[c][1] - agent.finals[c], 2) for _, c in watched}
    assert all(delay < 2.0 for delay in late.values()), late    # notified, not the 10 s fallback poll
    assert 0 < peak.peak <= BUDGET, f"peak {peak.peak} connections"
    print(f"peak connections: {peak.peak}; reply delivered after {late} s")
