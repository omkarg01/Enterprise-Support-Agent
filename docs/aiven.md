# Postgres on Aiven (free plan)

The app runs on any Postgres reachable by URL. This page covers Aiven's free plan, which replaced Neon:
1 CPU, 1 GB RAM, **1 GB disk**, Postgres **18**, no pooler in front, no high availability, and the service
powers off after a long idle period. `max_connections` is 20 with 3 kept for superusers, so `avnadmin` and
`app_login` (neither is one) share **17 slots**, one of which Aiven's own session holds: plan on 16.
The live service (checked 2026-11-26): `avnadmin` has `CREATEROLE`, `CREATEDB` and `BYPASSRLS` (no
superuser); only `defaultdb` (the app's main database) and Aiven's `_aiven` exist before the first migration.

## Setup

1. Create a free PostgreSQL service. The `pg_dump` you back up with must be at least the server's major
   version (it refuses a newer server): the app image installs `postgresql-client-18` from the PostgreSQL
   apt repository, and local development runs `postgres:18-alpine` too.
2. Copy the service URI. It looks like
   `postgresql://avnadmin:<password>@<service>-<project>.aivencloud.com:<port>/defaultdb?sslmode=require`.
3. Optional, for `sslmode=verify-full`: download the project's CA certificate (`ca.pem`), put it on the VM
   (for example `/opt/support-agent/aiven-ca.pem`, mounted read-only into the api container) and add
   `&sslrootcert=/path/in/container/aiven-ca.pem` to both URLs, or set `PGSSLROOTCERT`. libpq reads either;
   nothing in the code names a host.
4. Check that `avnadmin` can create roles and databases and whether it bypasses row-level security (this
   decides how data can be copied in, see the cutover below):
   `SELECT rolcreaterole, rolcreatedb, rolbypassrls FROM pg_roles WHERE rolname = current_user;`
   (all three are true on the free plan's `avnadmin`)
5. Run the migrations from the app image: `python deploy/vm/migrate.py`. It retries the first connection for
   30 s (a powered-off service starts), creates the roles `app_owner`, `app_runtime`, `legal_archive`,
   `vault_service`, `key_service` and the login `app_login` (no `BYPASSRLS`), the databases `agent_vault` and
   `agent_keys`, and applies every migration and module schema. Nothing needs a superuser and **no
   extension is needed**: the only non-core function used is `gen_random_uuid()`, built in since Postgres 13.

## Environment (VM `.env`)

| Variable | Value |
|---|---|
| `DATABASE_URL` | the Aiven URI with `avnadmin` (migrations, backups, `db_size.py`) |
| `APP_DATABASE_URL` | the same URI with user `app_login` and its password (what the API uses) |
| `VAULT_DATABASE`, `KEYS_DATABASE` | only if not `agent_vault` / `agent_keys` |
| `DB_POOL_MIN` / `DB_POOL_MAX` / `DB_STORE_POOL_MAX` | 1 / 8 / 2 (defaults) |
| `DB_DISK_LIMIT_MB` | 1024 (for the size warning) |
| `STAFF_EMAILS` | who may use the staff console, comma-separated |
| `PGSSLROOTCERT` | optional, instead of `sslrootcert=` in the URLs |

## Connection budget

Each API process (`deploy/vm/serve.py`) opens at most **11 connections**: the main pool
(`DB_POOL_MAX` 8: requests, sign-in, staff, the turn loop), the case-state pool (`DB_STORE_POOL_MAX` 2) and
one `LISTEN` connection that wakes open chat streams. Threads are capped (16 request workers, 8 sync-route
threads, 4 Temporal activities) so callers queue briefly for the pool; a caller that waits 10 s fails instead
of hanging. Scripts (`migrate.py` runs `psql` one session at a time, `seed_*.py`, `spend.py`,
`db_size.py`, `pgbackup.py daily`) use one connection each, so the worst case is about 13 (the API during a
rollout's migration plus the 02:30 backup) of the 16 available. A rollout stops the old api container before
starting the new one, so two API processes never overlap. Do not run a second API process (for example
`python -m channels.identity serve` on a laptop) against the same service: two processes are 22.
`tests/load/test_connection_budget.py` checks the 12-connection ceiling against a local Postgres started
with `max_connections=20`.

## Disk (1 GB)

`python deploy/vm/db_size.py` prints every database's size and the largest tables, and exits 1 at 80 % of
`DB_DISK_LIMIT_MB`. Empty schemas cost about 7.5 MB per database (about 23 MB for the three). **Nothing
purges on a schedule yet**: `outbound_event_log` (every stream event; meant to keep 7 days),
`conversation_messages`, `turns`, `decision_records`, `tool_audit_records`, `case_state`, `usage_ledger`,
`sessions` and `otp_challenges` only grow. Schedule `EventLog.purge_expired()` daily before the event log
gets large.

## Backups

The daily cron (`deploy/backup/support-agent-backup.cron`) still makes sense: it is an independent copy in
R2, outside Aiven, and it carries the erasure log that a restore must replay. With `DATABASE_URL` set,
`pgbackup.py daily` now dumps the Aiven databases with the host's `pg_dump` over TLS (the host needs a
`pg_dump` 18: on Oracle Linux 9, the PostgreSQL yum repository's `postgresql18` package). As of 2026-11-26
the cron is not installed on the VM and the host has no `pg_dump`, so nothing is backed up yet. The monthly `drill` restores into the **local** compose Postgres
(`docker compose -f deploy/compose.yaml --profile local-postgres up -d postgres`), never into Aiven: three
restored copies would not fit in 1 GB. The dump reads every tenant's rows, so `avnadmin` needs `BYPASSRLS`
(step 4); without it `pg_dump` stops with a row-level security error.

## Network

The API listens on `0.0.0.0:8000` because the Vercel frontend reaches it at `API_ORIGIN`, and Vercel has no
fixed outgoing addresses to allow-list. In the Oracle security list keep only `8000/tcp` from anywhere and
`22/tcp` from your own IP; everything else in `deploy/compose.yaml` binds to `127.0.0.1`. `/v1/staff/*` now
needs a staff sign-in. To take 8000 off the internet later, put Caddy on 443 in front (needs a domain name),
bind the API to `127.0.0.1`, and set `API_ORIGIN=https://<domain>` in Vercel.

## First start (an empty service)

Aiven started empty on 2026-11-26; nothing was copied from Neon, so earlier sign-ins, conversations and
tickets are gone and everyone signs in again.

1. Put the URLs and the staff list in the VM's `.env`: `DATABASE_URL` (the service URI, `avnadmin`,
   `defaultdb`, `?sslmode=require`), `APP_DATABASE_URL` (the same with user `app_login`: the migration creates
   it with `avnadmin`'s password, change it later with `ALTER ROLE app_login PASSWORD ...` and update the URL),
   `STAFF_EMAILS`.
2. Roll out: `python3 deploy/vm/release.py rollout <ref>`. Its migration step creates the roles, `app_login`,
   `agent_vault`, `agent_keys` and every table, then it swaps the api container and checks `/healthz`.
3. The tenants: `INSERT INTO tenants (id, name, region) VALUES ('t_us', 'Acme', 'US'), ('t_eu', 'Globex',
   'EU') ON CONFLICT DO NOTHING;` and the same two ids into `tenant_settings (tenant_id)`.
4. The demo systems and the knowledge base, in the app image:
   `docker compose -f deploy/compose.yaml -f deploy/vm/compose.vm.yaml --env-file .env run --rm api python
   deploy/vm/seed_nimbus.py t_us <customer emails>` and `... python deploy/vm/seed_kb.py`.
5. The user checks the live system by hand (CLAUDE.md: testing happens locally only).

## Copying from another Postgres (not used)

The rest of this page is how a copy from Neon would go, kept for a later move.

Do not write to Neon during the copy; stop the api container first.

1. `docker compose ... stop api` on the VM.
2. Dump Neon's three databases with a `pg_dump` of Neon's major version, from the **direct** host (drop
   `-pooler`): `pg_dump -Fc -d "$NEON_DIRECT_URL" -f agent.dump` (and the vault and keys databases).
3. Create the Aiven service (setup 1-4).
4. If `avnadmin` has `rolbypassrls`: create the roles and databases without tables
   (`python deploy/vm/migrate.py --bootstrap-only`), then
   `pg_restore --exit-on-error -d "$AIVEN_URL" agent.dump` (and vault, keys), then run
   `python deploy/vm/migrate.py`: it applies only what the dump does not already have.
   If it does not: run `python deploy/vm/migrate.py` on the empty service and seed it again
   (`deploy/vm/seed_nimbus.py`, `deploy/vm/seed_kb.py`). Conversations, tickets and sign-ins from Neon are
   not carried over.
5. Set `ALTER ROLE app_login PASSWORD '...'` on Aiven and put both URLs in the VM `.env`
   (`DATABASE_URL`, `APP_DATABASE_URL`).
6. Roll out (`deploy/vm/release.py`); it runs the migrations and checks `/healthz`.
7. Smoke test: sign in with a code on the portal, ask one question, watch the reply stream in, open the staff
   console as an address in `STAFF_EMAILS`, run `deploy/vm/db_size.py`, and check
   `SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()` stays at 11 or below.
8. Keep Neon read-only for a week, then delete it.
