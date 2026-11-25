<!-- genesis:start -->
## Genesis workflow

Before changing this repository, load the Genesis and Ponytail skills and read `.genesis/KICKOFF.md`. Obey its phase instruction: do not write product implementation code during discovery, specification, or planning. Use the Genesis CLI for tasks, proof, decisions, approvals, and checkpoints. End every work session with `genesis checkpoint .`.

### Ask the index before reading the code

This repository is indexed. Querying it is faster than grepping and answers questions grep cannot.

- `genesis query . search NAME` — where a name is defined, when you know the name but not the file
- `genesis query . scope PATH` — what a file or directory depends on, and what depends on it
- `genesis query . callers REF` / `callees REF` — call edges for a symbol
- `genesis query . impact PATH` — everything that transitively imports a file. Run this before editing shared code; it is the blast radius
- `genesis query . path FROM TO` — how two files are connected
- Add `--json` for machine-readable output.

Start from the scope cards and symptom map already in `genesis brief .`; they point at declarations before you read anything. Treat every answer as advisory: it is static analysis, so confirm in source before relying on it. A result marked `ambiguous` means several definitions matched and none were ruled out — check the candidates rather than assuming the first.

Run `genesis serve .` when the structure is unclear or you want to show a human what changed. It draws the repository as a live map, reindexes on save, and is read-only. If the index looks stale and nothing is watching, run `genesis index .`.

Hosts that speak MCP can mount the same questions as tools with `genesis mcp .`.
<!-- genesis:end -->

## Testing happens locally only

Production is tested by the user, by hand. Every automated test, feature check, load test, seed or experiment
runs on this machine, never against the deployed system. (Running all testing on the VM is what used up the
Neon free tier in a few days, November 2026.)

- Databases: only the local compose Postgres (`127.0.0.1:5432`, `docker compose -f deploy/compose.yaml up -d
  postgres`) or a throwaway local container (as `tests/load/test_connection_budget.py` starts). Never the managed
  database (Aiven, Neon) in the VM's `DATABASE_URL` / `APP_DATABASE_URL`.
- Keep `DATABASE_URL` and `APP_DATABASE_URL` empty in the local `.env`, so `database_url()` falls back to the
  local Postgres. Production credentials live only in the VM's `.env`. Before running anything that connects to
  a database, check that its DSN points at `127.0.0.1` or `localhost`.
- Never test against the VM or the live API: no `pytest`, `locust` (`tests/load/locustfile.py`), seed or ad-hoc
  scripts over SSH, and no requests to the public API or the Vercel site to check a feature.
- Touch the VM or production only when the user explicitly asks for that specific action (a rollout, a
  migration, a one-off fix), and never as a way to test.
