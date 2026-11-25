"""Daily Postgres backups to R2, the erasure log, and the scripted restore drill (NFR-19 / RP-ADP-04: RP-D11,
RP-D15, as the v1 profile does them: one VM, daily dumps rather than WAL streaming).

    python deploy/backup/pgbackup.py daily   dump agent, agent_vault and agent_keys to R2; prune past 35 days
    python deploy/backup/pgbackup.py drill   restore the latest dumps into restore_* databases and check them

- dump(): pg_dump -Fc of one database, stored write-once under backups/<database>/<UTC time>.dump with a
  manifest (SHA-256, size, applied migrations, row count per table at dump time).
- the erasure log: every completed erasure (tenant, principal, job, time) as a write-once object under
  erasure-log/, outside the databases, because a restored dump does not know about erasures made after it
  and the erasure ledger names users only by a hash. Kept as long as backups are (35 days, DP-D9).
- restore(): only into a database whose name starts with restore_ or standby_; the dump's SHA-256 must match
  its manifest. replay_erasures() then erases, in the restored copy, every user erased since the dump was
  taken (main data and case state, vault tokens, data keys: restoring agent_keys would otherwise bring
  shredded keys back) before anything uses it.
- drill(): restore all three, compare the migrations and row counts with the manifest, replay the erasures,
  verify that nothing of an erased user is left, and report.

With DATABASE_URL set (a managed server: Aiven, Neon), `daily` dumps that server's databases (the URL's,
VAULT_DATABASE, KEYS_DATABASE) with this host's pg_dump and psql over TLS (deploy/vm/migrate.py's PG*
variables; pg_dump must be at least the server's major version), and `drill` restores those dumps into the
LOCAL compose Postgres (docker compose --profile local-postgres up -d postgres): three restored copies would
not fit Aiven free's 1 GB disk, so the drill refuses a remote PSQL.
"""

import argparse
import asyncio
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from data.erasure import BACKUP_RETENTION_DAYS, ErasureRequest
from data.migrate import DEFAULT_PSQL, psql

DATABASES = (("agent", "main"), ("agent_vault", "vault"), ("agent_keys", "keys"))
DEFAULT_TOOLS = "docker exec -i support-agent-postgres-1"   # where pg_dump / pg_restore run
RESTORE_PREFIXES = ("restore_", "standby_")
STAMP = "%Y%m%dT%H%M%SZ"
REPLAY_MARGIN = timedelta(hours=1)    # an erasure finishing while the dump ran is replayed too


class BackupError(RuntimeError):
    pass


def _tool(name, *args, data=None):
    command = [*shlex.split(os.environ.get("PG_TOOLS", DEFAULT_TOOLS)), name,
               "-U", os.environ.get("PGUSER", "agent"), *args]
    result = subprocess.run(command, input=data, capture_output=True)
    if result.returncode != 0:
        raise BackupError(f"{name} failed: {result.stderr.decode(errors='replace').strip()[:500]}")
    return result.stdout


def snapshot(database):
    """(applied migrations, {table: rows}) of a database."""
    migrations = [row[0] for row in psql(database, "SELECT name FROM schema_migrations ORDER BY name")] \
        if psql(database, "SELECT to_regclass('schema_migrations')") != [[""]] else []
    tables = [row[0] for row in psql(database, "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                                               "ORDER BY tablename")]
    counts = {}
    for table in tables:
        counts[table] = int(psql(database, f'SELECT count(*) FROM "{table}"')[0][0])
    return migrations, counts


def dump(store, database, now=None):
    """Dump one database to the store; returns the dump's key."""
    now = now or datetime.now(timezone.utc)
    migrations, counts = snapshot(database)
    data = _tool("pg_dump", "-Fc", "-d", database)
    key = f"backups/{database}/{now.strftime(STAMP)}.dump"
    manifest = {"database": database, "created_at": now.isoformat(),
                "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "migrations": migrations,
                "counts": counts}
    store.put(key, data)
    store.put(key + ".json", json.dumps(manifest, sort_keys=True).encode())
    return key


def manifest(store, key):
    return json.loads(store.get(key + ".json"))


def latest(store, database):
    dumps = [k for k in store.keys(f"backups/{database}/") if k.endswith(".dump")]
    if not dumps:
        raise BackupError(f"no dump of {database}")
    return dumps[-1]


def prune(store, now=None, keep_days=BACKUP_RETENTION_DAYS):
    """Delete dumps and erasure-log entries past the retention window; a database's newest dump is kept."""
    horizon = (now or datetime.now(timezone.utc)) - timedelta(days=keep_days)
    removed = []
    for database in {k.split("/")[1] for k in store.keys("backups/")}:
        dumps = [k for k in store.keys(f"backups/{database}/") if k.endswith(".dump")]
        for key in dumps[:-1]:
            if datetime.fromisoformat(manifest(store, key)["created_at"]) < horizon:
                store.delete(key)
                store.delete(key + ".json")
                removed.append(key)
    for key in store.keys("erasure-log/"):
        if datetime.fromisoformat(json.loads(store.get(key))["completed_at"]) < horizon:
            store.delete(key)
            removed.append(key)
    return removed


# The erasure log --------------------------------------------------------------------------------------------

def log_erasure(store, tenant_id, principal_id, job_id, completed_at):
    """Record a completed erasure outside the databases (write-once: a job is logged once)."""
    entry = {"tenant_id": tenant_id, "principal_id": principal_id, "job_id": job_id,
             "completed_at": completed_at.isoformat()}
    store.put(f"erasure-log/{completed_at.strftime(STAMP)}-{job_id}.json", json.dumps(entry).encode())


def erasures_since(store, moment):
    entries = [json.loads(store.get(key)) for key in store.keys("erasure-log/")]
    return [e for e in entries if datetime.fromisoformat(e["completed_at"]) >= moment - REPLAY_MARGIN]


def replay_erasures(store, dumped_at, activities):
    """Erase again, in the restored databases, everyone erased since the dump. activities: a
    memory.lifecycle.inventory.ErasureActivities on the restored main, vault and key databases. Returns the
    residue per user ({} when nothing is left)."""
    residue = {}
    for entry in erasures_since(store, dumped_at):
        request = ErasureRequest(entry["tenant_id"], entry["principal_id"], "RESTORE_REPLAY")
        plan = asyncio.run(activities.collect(request))
        for target in ("postgres", "vault", "keys"):
            asyncio.run(activities.purge(target, request, plan))
        left = {k: v for k, v in activities._verify(request, plan).items() if v}
        if left:
            residue[entry["job_id"]] = left
    return residue


# Restore and drill ------------------------------------------------------------------------------------------

def restore(store, key, target):
    """Restore a dump into `target` (dropped and created again); refuses any name that is not a restore
    copy."""
    if not target.startswith(RESTORE_PREFIXES):
        raise BackupError(f"refusing to restore into {target!r}: only restore_* or standby_* databases")
    data = store.get(key)
    if hashlib.sha256(data).hexdigest() != manifest(store, key)["sha256"]:
        raise BackupError(f"{key} does not match its manifest")
    psql("postgres", f'DROP DATABASE IF EXISTS "{target}" WITH (FORCE)')
    psql("postgres", f'CREATE DATABASE "{target}"')
    _tool("pg_restore", "--exit-on-error", "-d", target, data=data)


@dataclass
class DrillReport:
    restored: dict = field(default_factory=dict)       # database -> (dump key, restored as)
    mismatches: list = field(default_factory=list)
    erasures_replayed: int = 0
    residue: dict = field(default_factory=dict)
    seconds: float = 0.0
    expected: int = len(DATABASES)

    @property
    def ok(self):
        return not self.mismatches and not self.residue and len(self.restored) == self.expected


def drill(store, activities_for, databases=DATABASES, prefix="restore_", keep=False):
    """The restore drill. activities_for(restored names by kind) -> ErasureActivities on the restored
    copies; built only when there are erasures to replay. The copies hold real customer data, so they are
    dropped afterwards unless keep is set (a warm standby must then be in the erasure fan-out, RP-D15)."""
    started, report, names = time.monotonic(), DrillReport(expected=len(databases)), {}
    try:
        _drill(store, activities_for, databases, prefix, report, names)
    finally:
        if not keep:
            for target in names.values():
                psql("postgres", f'DROP DATABASE IF EXISTS "{target}" WITH (FORCE)')
    report.seconds = time.monotonic() - started
    return report


def _drill(store, activities_for, databases, prefix, report, names):
    oldest = None
    for database, kind in databases:
        key = latest(store, database)
        target = names[kind] = prefix + database
        restore(store, key, target)
        expected = manifest(store, key)
        migrations, counts = snapshot(target)
        if migrations != expected["migrations"]:
            report.mismatches.append(f"{database}: migrations differ")
        report.mismatches += [f"{database}.{t}: {counts.get(t)} rows, {n} at dump time"
                              for t, n in expected["counts"].items() if counts.get(t) != n]
        report.restored[database] = (key, target)
        created = datetime.fromisoformat(expected["created_at"])
        oldest = created if oldest is None else min(oldest, created)
    report.erasures_replayed = len(erasures_since(store, oldest))
    if report.erasures_replayed:
        report.residue = replay_erasures(store, oldest, activities_for(names))


def main(argv=None):  # pragma: no cover - runs on the VM with R2 credentials
    parser = argparse.ArgumentParser(description="Daily Postgres backups to R2 and the restore drill")
    parser.add_argument("action", choices=["daily", "drill"])
    action = parser.parse_args(argv).action

    from data.storage import r2_store_from_secrets
    store = r2_store_from_secrets()
    databases = DATABASES
    if os.environ.get("DATABASE_URL"):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vm"))
        from migrate import psql_environment  # deploy/vm/migrate.py

        main_db, env = psql_environment(os.environ["DATABASE_URL"])
        databases = ((main_db, "main"), (os.environ.get("VAULT_DATABASE", "agent_vault"), "vault"),
                     (os.environ.get("KEYS_DATABASE", "agent_keys"), "keys"))
        if action == "daily":
            os.environ.update(env)
            os.environ.setdefault("PSQL", "psql")
            os.environ.setdefault("PG_TOOLS", "")
    if action == "daily":
        for database, _ in databases:
            print("dumped", dump(store, database))
        print("pruned", prune(store))
        return 0

    import psycopg

    from core.secrets import OpenBao
    from data.keys import KeyStore, OpenBaoTransit
    from memory.lifecycle.inventory import ErasureActivities

    remote = os.environ.get("PGHOST") not in (None, "", "127.0.0.1", "localhost")
    if os.environ.get("PSQL", DEFAULT_PSQL) != DEFAULT_PSQL or remote:
        raise SystemExit("the drill restores into the local compose Postgres only (unset PSQL and PGHOST)")
    opened = []  # the drill's key-store connections, closed when it ends

    def activities_for(names):
        base = os.environ["RESTORE_DSN_BASE"]  # e.g. postgresql://agent:<password>@127.0.0.1:5432/
        transit = OpenBaoTransit(OpenBao())
        opened.append(psycopg.connect(base + names["keys"], autocommit=True))

        def keys():
            return KeyStore(opened[-1], transit)
        # no Temporal client and no receipts: the replay only purges and verifies the restored copies
        return ErasureActivities(base + names["main"], base + names["vault"], keys, None, b"restore-replay")
    try:
        report = drill(store, activities_for, databases)
    finally:
        for conn in opened:
            conn.close()
    print(json.dumps({"ok": report.ok, "restored": report.restored, "mismatches": report.mismatches,
                      "erasures_replayed": report.erasures_replayed, "residue": report.residue,
                      "seconds": round(report.seconds, 1)}, indent=2))
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
