"""Seed the public knowledge base from deploy/vm/kb/kb-*.md (M7-01): `python deploy/vm/seed_kb.py` in the app
image (`docker compose ... run --rm api python deploy/vm/seed_kb.py`) or on a laptop, where unset settings come
from the repository's .env and Ollama defaults to the compose one on 127.0.0.1.

Each article's front matter labels it (doc_id, title, source_system, source_kind, audience, provenance,
acl_groups, is_restricted, updated_at); the body after it is what is chunked and embedded, with the model pinned
in deploy/vm/models.json, and indexed into the public collection through knowledge.ingest, one batch per source
system, so a rerun replaces the articles in place and an article removed from the folder is purged.

The public collection holds every article, tickets included: all of them are customer_visible (2026-11-18,
the user's decision: a customer's answer cites every article it uses). A restricted article (is_restricted)
is indexed too but never read for a customer: retrieval filters it out, and the B26 probe sees only its id
and acl_groups, to hand the turn to that team. The source "seed_kb" (the first, unlabelled articles) is synced
empty, which purges them from an index seeded before.
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KB = Path(__file__).with_name("kb")
LEGACY = "seed_kb"
FIELDS = ("doc_id", "title", "source_system", "source_kind", "audience", "provenance", "acl_groups",
          "is_restricted", "updated_at")


def front_matter(markdown):
    """(labels, body) of an article opening with a `---` block of `key: value` lines."""
    _, head, body = markdown.split("---\n", 2)
    labels = {}
    for line in head.splitlines():
        key, _, value = line.partition(":")
        value = value.strip()
        if value.startswith("["):
            value = [item.strip().strip("\"'") for item in value.strip("[]").split(",") if item.strip()]
        elif value in ("true", "false"):
            value = value == "true"
        labels[key.strip()] = value
    return labels, body.lstrip()


def documents(folder=KB):
    """The folder's articles as documents."""
    from knowledge.ingest.pipeline import Document

    public = []
    for path in sorted(folder.glob("kb-*.md")):
        labels, body = front_matter(path.read_text(encoding="utf-8").replace("\r\n", "\n"))
        public.append(Document(markdown=body, **{key: labels[key] for key in FIELDS}))
    return public


def seed(client, embed, dimensions, conn, folder=KB, namespace=""):
    from knowledge.index.store import Index
    from knowledge.ingest.pipeline import Ingestion

    ingestion = Ingestion(Index(client, dimensions, namespace), embed, conn)
    public = documents(folder)
    reports = {source: ingestion.run_batch("public", source, public)
               for source in sorted({LEGACY} | {d.source_system for d in public})}
    return {"indexed": sum(r["indexed"] for r in reports.values()),
            "purged_missing": sorted(d for r in reports.values() for d in r["purged_missing"]),
            "by_source": reports}


def main():
    sys.path[:0] = [str(ROOT / "src")]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key and not key.startswith("#"):
                os.environ.setdefault(key.strip(), value.strip())
    try:  # a laptop whose antivirus or proxy inspects HTTPS: verify against the OS store (register B4)
        import truststore

        truststore.inject_into_ssl()
    except ImportError:
        pass
    import psycopg
    from qdrant_client import QdrantClient

    from runtime.production import ollama_embed, qdrant_url

    lock = json.loads((ROOT / "deploy" / "vm" / "models.json").read_text(encoding="utf-8"))["embedding"]
    client = QdrantClient(url=qdrant_url(os.environ),
                          api_key=os.environ.get("QDRANT_API_KEY") or None)
    ollama = os.environ.get("OLLAMA_URL") or ("http://ollama:11434" if Path("/.dockerenv").exists()
                                              else "http://127.0.0.1:11434")
    embed = ollama_embed(ollama, lock["model"])
    # prepare_threshold=0: safe behind a transaction pooler (Neon's -pooler host)
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, prepare_threshold=0) as conn:
        print(json.dumps(seed(client, embed, lock["dimensions"], conn), indent=1), flush=True)


if __name__ == "__main__":
    main()
