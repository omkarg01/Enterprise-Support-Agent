"""python -m channels.identity migrate   create the databases and every table the API needs
python -m channels.identity serve     run the API with sign-in on http://127.0.0.1:8000

Development server: the signing key and the mock SSO provider's key are generated at start (tokens stop
working after a restart), and one-time codes are printed to the console instead of being emailed.
"""

import os
import sys

import uvicorn

from channels import sessions
from channels.api import turns
from channels.api.app import database_url
from channels.identity import otp
from channels.identity.oidc import MockIdP
from channels.identity.server import build_app
from channels.identity.service import Identity
from data import events, migrate
from data.pool import pool


def print_code(tenant_id, user_id, code):
    print(f"[one-time code] {tenant_id}/{user_id}: {code}", flush=True)


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if command == "migrate":
        migrate.migrate_all()
        for module in (events, turns, sessions, otp):
            module.apply_schema(migrate.DEFAULT_DATABASES[0])
    elif command == "serve":
        idp = MockIdP()
        identity = Identity(pool(database_url()).connection,  # the API's own pool (data.pool)
                            idp=(idp.issuer, idp.public_key), send_otp=print_code)
        host = os.environ.get("SUPPORT_AGENT_HOST", "127.0.0.1")
        port = int(os.environ.get("SUPPORT_AGENT_PORT", "8000"))
        uvicorn.run(build_app(None, identity, mock_idp=idp), host=host, port=port)
    else:
        sys.exit(__doc__)
