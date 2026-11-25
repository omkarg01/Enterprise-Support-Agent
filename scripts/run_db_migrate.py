"""Apply migrations to the managed Postgres in DATABASE_URL (Aiven, Neon) from a laptop, with psql in the
compose Postgres container. A partial copy: on the VM, deploy/vm/migrate.py is the one to run."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]

ROLE_BOOTSTRAP = """
GRANT USAGE, CREATE ON SCHEMA public TO CURRENT_USER;
GRANT USAGE, CREATE ON SCHEMA public TO app_owner;
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_owner') THEN CREATE ROLE app_owner NOLOGIN; END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_runtime') THEN CREATE ROLE app_runtime NOLOGIN; END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'legal_archive') THEN CREATE ROLE legal_archive NOLOGIN; END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'vault_service') THEN CREATE ROLE vault_service NOLOGIN; END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'key_service') THEN CREATE ROLE key_service NOLOGIN; END IF;
END $$;
GRANT app_owner, app_runtime, legal_archive, vault_service, key_service TO CURRENT_USER;
GRANT USAGE, CREATE ON SCHEMA public TO app_owner;
"""


def load_dotenv(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key, value)


def configure_psql_from_database_url() -> str:
    url = urlparse(os.environ["DATABASE_URL"])
    if url.scheme not in ("postgresql", "postgres"):
        raise SystemExit(f"unsupported DATABASE_URL scheme: {url.scheme}")
    main_db = (url.path or "/").lstrip("/") or "postgres"
    host = url.hostname or ""
    host = host.replace("-pooler", "", 1)  # Neon's pooled host; a no-op for every other provider
    os.environ["PGHOST"] = host
    os.environ["PGPORT"] = str(url.port or 5432)
    os.environ["PGUSER"] = unquote(url.username or "")
    os.environ["PGPASSWORD"] = unquote(url.password or "")
    qs = parse_qs(url.query)
    os.environ["PGSSLMODE"] = qs.get("sslmode", ["require"])[0]
    user = os.environ["PGUSER"]
    os.environ["PSQL"] = (
        "docker exec -i -e PGHOST -e PGPORT -e PGUSER -e PGPASSWORD -e PGSSLMODE "
        f"support-agent-postgres-1 psql -U {user}"
    )
    return main_db


def main() -> int:
    os.chdir(ROOT)
    load_dotenv(ROOT / ".env")
    if not os.environ.get("DATABASE_URL"):
        raise SystemExit("DATABASE_URL is not set in .env")
    main_db = configure_psql_from_database_url()
    vault_db, keys_db = "agent_vault", "agent_keys"
    sys.path.insert(0, str(ROOT / "src"))

    from channels import sessions
    from channels.api import turns
    from channels.identity import otp
    from data import events, migrate

    print(f"Migrating main={main_db}, vault={vault_db}, keys={keys_db} on {os.environ['PGHOST']} …")
    migrate.psql(main_db, ROLE_BOOTSTRAP)
    for database in (vault_db, keys_db):
        migrate.ensure_database(database, maintenance_db=main_db)
        migrate.psql(database, ROLE_BOOTSTRAP)
    migrate.migrate_all((main_db, vault_db, keys_db))
    for module in (events, turns, sessions, otp):
        module.apply_schema(main_db)
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
