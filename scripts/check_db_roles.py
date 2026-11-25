import os
from pathlib import Path

import psycopg

root = Path(__file__).resolve().parents[1]
for line in (root / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    os.environ.setdefault(key, value)

url = os.environ["DATABASE_URL"]
with psycopg.connect(url) as conn:
    rows = conn.execute(
        "SELECT rolname FROM pg_roles WHERE rolname IN ('app_runtime', 'app_owner')"
    ).fetchall()
    print("roles", rows)
    print("db", conn.info.dbname)
    conn.execute("SET ROLE app_runtime")
    print("set role ok")
