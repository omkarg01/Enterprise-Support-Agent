"""Seed the public knowledge base from deploy/vm/kb/*.md (M7-01): `python deploy/vm/seed_kb.py` in the app
image (`docker compose ... run --rm api python deploy/vm/seed_kb.py`).

Each article is a curated, customer-visible document of source "seed_kb", embedded with the model pinned in
deploy/vm/models.json and indexed into the public collection through knowledge.ingest, so a rerun replaces the
articles in place and an article removed from the folder is purged from the index.
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KB = Path(__file__).with_name("kb")
SOURCE = "seed_kb"


def documents(folder=KB):
    from knowledge.ingest.pipeline import Document

    out = []
    for path in sorted(folder.glob("*.md")):
        markdown = path.read_text(encoding="utf-8")
        title = markdown.splitlines()[0].lstrip("# ").strip()
        out.append(Document(doc_id=f"kb-{path.stem}", source_system=SOURCE, source_kind="curated",
                            title=title, markdown=markdown, audience="customer_visible", provenance="human"))
    return out


def seed(client, embed, dimensions, conn, folder=KB, namespace=""):
    from knowledge.index.store import Index
    from knowledge.ingest.pipeline import Ingestion

    return Ingestion(Index(client, dimensions, namespace), embed, conn).run_batch("public", SOURCE,
                                                                                documents(folder))


def main():
    sys.path[:0] = [str(ROOT / "src")]
    import psycopg
    from qdrant_client import QdrantClient

    from runtime.production import ollama_embed, qdrant_url

    lock = json.loads((ROOT / "deploy" / "vm" / "models.json").read_text(encoding="utf-8"))["embedding"]
    client = QdrantClient(url=qdrant_url(os.environ),
                          api_key=os.environ.get("QDRANT_API_KEY") or None)
    embed = ollama_embed(os.environ.get("OLLAMA_URL", "http://ollama:11434"), lock["model"])
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        print(json.dumps(seed(client, embed, lock["dimensions"], conn)), flush=True)


if __name__ == "__main__":
    main()
