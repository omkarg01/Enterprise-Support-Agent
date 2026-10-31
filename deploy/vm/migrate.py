"""Every migration on the deployed databases: `python deploy/vm/migrate.py` in the app image (AC-15).

DATABASE_URL names the main database. psql connects to its direct endpoint (Neon's "-pooler" host is a
transaction pooler: migrations go around it). The vault and key-store databases sit next to the main one
(VAULT_DATABASE / KEYS_DATABASE, default agent_vault / agent_keys).

1. the application roles (app_owner, app_runtime, legal_archive, vault_service, key_service) exist and the
   connecting user is a member, so it can hand tables to app_owner;
2. the vault and key-store databases exist;
3. migrations/main, vault and keys are applied (data.migrate: each once, in its own transaction);
4. every module-owned SCHEMA (a module defining SCHEMA_NAME and apply_schema) is applied to the main
   database. Modules reference each other's tables, so they are applied in rounds until none is pending; a
   round that applies nothing stops with the errors.

Migrations only expand (tests/contracts/test_policy_lints.py), so the running release keeps working.
"""

import importlib
import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parents[2]
ROLES = ("app_owner", "app_runtime", "legal_archive", "vault_service", "key_service")
CREATE_ROLE = ("  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{0}') THEN CREATE ROLE {0} NOLOGIN; "
               "END IF;\n")
BOOTSTRAP = ("DO $$\nBEGIN\n" + "".join(CREATE_ROLE.format(role) for role in ROLES) + "END $$;\n"
             + f"GRANT {', '.join(ROLES)} TO CURRENT_USER;\n"
             + "GRANT USAGE, CREATE ON SCHEMA public TO app_owner;\n")


def psql_environment(url):
    """(main database, PG* variables) for psql from DATABASE_URL, on the direct (not pooled) host."""
    parts = urlparse(url)
    if parts.scheme not in ("postgresql", "postgres"):
        raise SystemExit(f"DATABASE_URL must be a postgresql:// URL, not {parts.scheme}://")
    host = (parts.hostname or "").replace("-pooler", "", 1)
    sslmode = parse_qs(parts.query).get("sslmode", ["prefer" if host == "127.0.0.1" else "require"])[0]
    env = {"PGHOST": host, "PGPORT": str(parts.port or 5432), "PGUSER": unquote(parts.username or ""),
           "PGPASSWORD": unquote(parts.password or ""), "PGSSLMODE": sslmode}
    return (parts.path or "/").lstrip("/") or "postgres", env


def module_schemas(roots=(ROOT / "src", ROOT / "evals")):
    """Modules owning a SCHEMA, as importable names, in a stable order."""
    names = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if not re.search(r"^SCHEMA_NAME\s*=", path.read_text(encoding="utf-8"), re.M):
                continue
            base = root.parent if root.name == "evals" else root       # evals is a top-level package
            parts = list(path.relative_to(base).with_suffix("").parts)
            names.append(".".join(parts[:-1] if parts[-1] == "__init__" else parts))
    return names


def apply_in_rounds(modules, database, error_type=Exception):
    """apply_schema(database) on each module until all are applied; returns the rounds' module lists."""
    pending, rounds = list(modules), []
    while pending:
        applied, errors = [], {}
        for module in pending:
            try:
                module.apply_schema(database)
                applied.append(module)
            except error_type as error:
                errors[module.__name__] = str(error).splitlines()[0] if str(error) else type(error).__name__
        if not applied:
            raise SystemExit("schemas that cannot be applied: "
                             + "; ".join(f"{name}: {error}" for name, error in errors.items()))
        rounds.append([m.__name__ for m in applied])
        pending = [m for m in pending if m not in applied]
    return rounds


def main():
    sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
    main_db, env = psql_environment(os.environ["DATABASE_URL"])
    os.environ.update(env)
    os.environ.setdefault("PSQL", "psql")
    from data import migrate

    vault = os.environ.get("VAULT_DATABASE", "agent_vault")
    keys = os.environ.get("KEYS_DATABASE", "agent_keys")
    migrate.psql(main_db, BOOTSTRAP)
    for database in (vault, keys):
        migrate.ensure_database(database, maintenance_db=main_db)
        migrate.psql(database, BOOTSTRAP)
    migrate.migrate_all((main_db, vault, keys))       # prints each migration it applies
    modules = [importlib.import_module(name) for name in module_schemas()]
    rounds = apply_in_rounds(modules, main_db, migrate.PsqlError)
    print(f"{len(modules)} module schemas checked in {len(rounds)} round(s)", flush=True)


if __name__ == "__main__":
    main()
