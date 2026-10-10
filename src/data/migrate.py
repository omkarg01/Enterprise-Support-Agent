"""Apply the SQL migrations in migrations/<kind>/ to the main, vault and key-store databases.

    python -m data.migrate                       # agent, agent_vault, agent_keys
    python -m data.migrate MAIN VAULT KEYS       # other database names

SQL runs through psql. By default that is the psql inside the compose Postgres container; set PSQL to another
command (e.g. "psql" with PGHOST/PGUSER/PGPASSWORD set) to target Neon, Supabase or a host install.
"""

import os
import shlex
import subprocess
import sys
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
KINDS = ("main", "vault", "keys")
DEFAULT_DATABASES = ("agent", "agent_vault", "agent_keys")
DEFAULT_PSQL = "docker exec -i support-agent-postgres-1 psql -U agent"


class PsqlError(RuntimeError):
    pass


def psql(database, sql):
    """Run SQL in one psql session (stops at the first error) and return unaligned rows."""
    command = shlex.split(os.environ.get("PSQL", DEFAULT_PSQL))
    result = subprocess.run(
        [*command, "-X", "-q", "-A", "-t", "-F", "|", "-v", "ON_ERROR_STOP=1", "-d", database],
        input=sql, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise PsqlError(result.stderr.strip())
    return [line.split("|") for line in result.stdout.splitlines() if line]


def ensure_database(name, maintenance_db):
    if not psql(maintenance_db, f"SELECT 1 FROM pg_database WHERE datname = '{name}'"):
        psql(maintenance_db, f'CREATE DATABASE "{name}"')


def migrate(kind, database):
    """Apply each not-yet-applied migration of a kind in its own transaction; return the names applied."""
    psql(database, "CREATE TABLE IF NOT EXISTS schema_migrations "
                   "(name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
    done = {row[0] for row in psql(database, "SELECT name FROM schema_migrations")}
    applied = []
    for path in sorted((MIGRATIONS / kind).glob("*.sql")):
        if path.name not in done:
            psql(database, f"BEGIN;\n{path.read_text()}\n"
                           f"INSERT INTO schema_migrations (name) VALUES ('{path.name}');\nCOMMIT;\n")
            applied.append(path.name)
    return applied


def migrate_all(databases=DEFAULT_DATABASES):
    for kind, database in zip(KINDS, databases, strict=True):
        if kind != "main":
            ensure_database(database, maintenance_db=databases[0])
        for name in migrate(kind, database):
            print(f"{database}: applied {name}")


if __name__ == "__main__":
    migrate_all(tuple(sys.argv[1:]) or DEFAULT_DATABASES)
