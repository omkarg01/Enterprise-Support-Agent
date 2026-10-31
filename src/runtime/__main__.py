"""python -m runtime serve   the API with sign-in and the turn loop on http://127.0.0.1:8000

For a laptop: settings come from the environment, then from the repository's .env (QDRANT_URL, OLLAMA_URL and
DATABASE_URL default to the local compose stack). Needs PII_TOKEN_KEY and at least one model key (GROQ_API_KEY
or GEMINI_API_KEY) for answers; without a model key every turn is handed to a person.
"""

import os
import sys
from pathlib import Path

import uvicorn

from runtime.production import app

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

if __name__ == "__main__":
    if (sys.argv[1] if len(sys.argv) > 1 else "serve") != "serve":
        sys.exit(__doc__)
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key and not key.startswith("#"):
                os.environ.setdefault(key.strip(), value.strip())
    uvicorn.run(app(), host=os.environ.get("SUPPORT_AGENT_HOST", "127.0.0.1"),
                port=int(os.environ.get("SUPPORT_AGENT_PORT", "8000")))
