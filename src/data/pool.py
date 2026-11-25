"""Process-wide Postgres connection pools, sized for a small server (Aiven free: max_connections 20, a few of
them reserved, no pooler in front).

Connection budget of one API process: DB_POOL_MAX (8: requests, sign-in, staff, the turn loop) +
DB_STORE_POOL_MAX (2: the statechart's case state) + 1 LISTEN connection (data.events.listener) = 11 <= 12.
The pools enforce it whatever the thread counts are; channels.api.app and workflows.activities cap their
threads so callers do not queue far beyond it. A script opens one connection of its own in a with-block.

Every pool checks a connection before lending it (a restart or power-off leaves dead sockets behind, and the
pool opens new ones in the background), drops idle ones after 5 minutes and recycles each after 30; a caller
waits at most 10 s for one, then gets psycopg_pool.PoolTimeout instead of hanging.

TLS is libpq's: put sslmode=require (and sslrootcert=/path/to/ca.pem for verify-full) in the URL's query, or
set PGSSLMODE / PGSSLROOTCERT. Nothing here names a host.
"""

import logging
import os
import threading
import time

import psycopg
from psycopg_pool import ConnectionPool

log = logging.getLogger(__name__)
_pools: dict = {}
_lock = threading.Lock()


def _size(name, default):
    return int(os.environ.get(name, default))


def pool(dsn, kind="main"):
    """The process's pool of `kind` for dsn, created on first use: "main" holds DB_POOL_MIN 1 to
    DB_POOL_MAX 8; any other kind (the API's "store", a separate process's own) at most
    DB_STORE_POOL_MAX 2."""
    with _lock:
        if (kind, dsn) not in _pools:
            high = _size("DB_POOL_MAX", 8) if kind == "main" else _size("DB_STORE_POOL_MAX", 2)
            low = min(_size("DB_POOL_MIN", 1), high)
            _pools[kind, dsn] = ConnectionPool(
                dsn, min_size=low, max_size=high, max_idle=300, max_lifetime=1800, timeout=10,
                check=ConnectionPool.check_connection, name=f"{kind}-pool", open=True,
                kwargs={"autocommit": True, "prepare_threshold": 0})
        return _pools[kind, dsn]


def wait_for(dsn, attempts=5, delay=2.0, connect=psycopg.connect, sleep=time.sleep):
    """Return once the server takes a connection, retrying with doubling delays (2 + 4 + 8 + 16 = 30 s over 5
    attempts by default): a powered-off or restarting server is let come back before startup gives up."""
    for attempt in range(1, attempts + 1):
        try:
            with connect(dsn, connect_timeout=10):
                return
        except psycopg.OperationalError as error:
            if attempt == attempts:
                raise
            log.warning("database not reachable (attempt %d of %d), retrying in %.0f s: %s",
                        attempt, attempts, delay, str(error).splitlines()[0] if str(error) else error)
            sleep(delay)
            delay *= 2
