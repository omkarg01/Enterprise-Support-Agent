"""The API as the VM serves it (NFR-36, AC-15): `python deploy/vm/serve.py`, the api container's command.

Unlike the development server (python -m channels.identity serve) it has no mock SSO route: SSO sign-in is
refused until a real provider is configured, so customers sign in anonymously or with a one-time code; and it
refuses to start with development header sign-in on. /healthz checks the database, for the rollout.

The session signing key is generated at start, so a restart signs everyone out; one-time codes go to the
container log (there is no mail sender yet); turns are accepted and recorded, nothing runs them yet (the turn
loop is not wired). See the open-issues register.
"""

import os
import sys

import psycopg
import uvicorn

from channels.api.app import database_url
from channels.identity.server import build_app
from channels.identity.service import Identity


def log_code(tenant_id, user_id, code):
    print(f"[one-time code] {tenant_id}/{user_id}: {code}", flush=True)


def app(environ=os.environ):
    if environ.get("SUPPORT_AGENT_DEV_AUTH") == "1":
        raise SystemExit("refusing to serve with SUPPORT_AGENT_DEV_AUTH=1: anyone could sign in with a "
                         "header")

    def connect():
        return psycopg.connect(database_url(), autocommit=True)

    api = build_app(None, Identity(connect, idp=None, send_otp=log_code))

    @api.get("/healthz")
    def healthz():
        with connect() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok", "revision": environ.get("APP_REVISION", "unknown")}

    return api


if __name__ == "__main__":
    port = int(os.environ.get("SUPPORT_AGENT_PORT", "8000"))
    uvicorn.run(app(), host=os.environ.get("SUPPORT_AGENT_HOST", "0.0.0.0"), port=port, proxy_headers=False)
    sys.exit(0)
