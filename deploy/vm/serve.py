"""The API as the VM serves it (NFR-36, AC-15): `python deploy/vm/serve.py`, the api container's command.

runtime.production.app(): sign-in (anonymous and one-time code; no mock SSO route, SSO refused until a real
provider is configured; it refuses to start with development header sign-in on), the turn loop that answers
accepted turns (runtime), and /healthz (the database), which the rollout checks.

The session signing key is generated at start, so a restart signs everyone out; one-time codes go to the
container log (there is no mail sender yet). See the open-issues register.
"""

import os

import uvicorn

from runtime.production import app

if __name__ == "__main__":
    uvicorn.run(app(), host=os.environ.get("SUPPORT_AGENT_HOST", "0.0.0.0"),
                port=int(os.environ.get("SUPPORT_AGENT_PORT", "8000")), proxy_headers=False)
