#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
sed -i 's/\r$//' .env 2>/dev/null || true
# Bash `source .env` breaks on `&` in DATABASE_URL query strings; load like scripts/check_db_roles.py.
eval "$("$ROOT/.venv/bin/python" - <<'PY'
import shlex
from pathlib import Path

for line in (Path(".") / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    print(f"export {key}={shlex.quote(value)}")
PY
)"
export PYTHONPATH=src
export SUPPORT_AGENT_HOST="${SUPPORT_AGENT_HOST:-0.0.0.0}"
export SUPPORT_AGENT_PORT="${SUPPORT_AGENT_PORT:-8000}"
exec "$ROOT/.venv/bin/python" -m channels.identity serve
