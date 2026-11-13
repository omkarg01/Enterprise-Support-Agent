"""Seed the demo Nimbus systems (AGENT-01) for the customers who will try the agent's tools:
`python deploy/vm/seed_nimbus.py TENANT PRINCIPAL [PRINCIPAL ...]` in the app image
(`docker compose ... run --rm api python deploy/vm/seed_nimbus.py t_us sarah@example.com`) or on a laptop,
where an unset DATABASE_URL comes from the repository's .env. PRINCIPAL is the user id the customer signs in
with. Running it again changes nothing (tools.nimbus.seed). Run deploy/vm/migrate.py first.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main(argv=sys.argv[1:]):
    if len(argv) < 2:
        raise SystemExit(__doc__)
    sys.path[:0] = [str(ROOT / "src")]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key and not key.startswith("#"):
                os.environ.setdefault(key.strip(), value.strip())
    import psycopg

    from tools.nimbus import seed

    tenant, principals = argv[0], argv[1:]

    def connect():  # prepare_threshold=0: Neon's pooled endpoint is a transaction pooler
        return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, prepare_threshold=0)
    for principal in principals:
        seed(connect, tenant, principal)
        print(f"seeded {tenant}/{principal}", flush=True)


if __name__ == "__main__":
    main()
