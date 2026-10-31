"""In-place re-index of the knowledge collections for a new pinned embedding model (NFR-29 / DL-ADP-04:
DL-D11): `python deploy/vm/reindex.py`, run by release.py --maintenance before the api swap.

For each knowledge collection (the public one and each tenant's private one), every chunk is read and
re-embedded with the model pinned in deploy/vm/models.json first; only when all of them have new vectors is
the collection dropped and rebuilt with the new dimensions (knowledge.index.Index.ensure: the same layout,
sparse BM25 vectors and payload indexes) and the chunks written back with their payloads unchanged. A model
that fails part-way therefore leaves the index as it was. While a collection is rebuilt, its searches find
less or nothing: the accepted maintenance-window degradation (KU1), instead of a second cluster.
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BATCH = 64


def knowledge_scope(name, namespace=""):
    """'public', a tenant id, or None for a collection that is not a knowledge collection."""
    from knowledge.index.store import VERSION

    if not name.startswith(namespace):
        return None
    rest = name[len(namespace):]
    if rest == f"public_knowledge_{VERSION}":
        return "public"
    if rest.startswith("tenant_") and rest.endswith(f"_private_{VERSION}"):
        return rest[len("tenant_"):-len(f"_private_{VERSION}")]
    return None


def chunks(client, name):
    """Every point of a collection as (id, payload)."""
    out, offset = [], None
    while True:
        points, offset = client.scroll(name, limit=256, offset=offset, with_payload=True, with_vectors=False)
        out += [(point.id, point.payload) for point in points]
        if offset is None:
            return out


def reindex(client, embed, dimensions, namespace=""):
    """Rebuild every knowledge collection with embed(texts) -> vectors of `dimensions`; returns
    {collection: chunks rewritten}."""
    from qdrant_client import models

    from knowledge.index.store import Index, sparse

    plans = {}
    for description in client.get_collections().collections:
        scope = knowledge_scope(description.name, namespace)
        if scope is None:
            continue
        points = chunks(client, description.name)
        vectors = []
        for start in range(0, len(points), BATCH):
            vectors += embed([payload["text"] for _, payload in points[start:start + BATCH]])
        if len(vectors) != len(points) or any(len(v) != dimensions for v in vectors):
            raise ValueError(f"{description.name}: the model returned vectors of the wrong number or size")
        plans[description.name] = (scope, points, vectors)
    index = Index(client, dimensions, namespace)
    for name, (scope, points, vectors) in plans.items():
        client.delete_collection(name)
        index.ensure(scope)
        for start in range(0, len(points), BATCH):
            batch = zip(points[start:start + BATCH], vectors[start:start + BATCH], strict=True)
            client.upsert(name, wait=True, points=[
                models.PointStruct(id=point_id, vector={"dense": vector, "bm25": sparse(payload["text"])},
                                   payload=payload)
                for (point_id, payload), vector in batch])
    return {name: len(points) for name, (_, points, _) in plans.items()}


def ollama_embed(base_url, model, timeout=120.0):
    import httpx

    def embed(texts):
        reply = httpx.post(f"{base_url}/api/embed", json={"model": model, "input": texts}, timeout=timeout)
        reply.raise_for_status()
        return reply.json()["embeddings"]
    return embed


def main():
    sys.path[:0] = [str(ROOT / "src")]
    from qdrant_client import QdrantClient

    lock = json.loads((ROOT / "deploy" / "vm" / "models.json").read_text(encoding="utf-8"))["embedding"]
    if lock["provider"] != "ollama":
        raise SystemExit(f"no embedder for provider {lock['provider']!r}")
    client = QdrantClient(url=os.environ.get("QDRANT_URL", "http://qdrant:6333"),
                          api_key=os.environ.get("QDRANT_API_KEY") or None)
    embed = ollama_embed(os.environ.get("OLLAMA_URL", "http://ollama:11434"), lock["model"])
    print(json.dumps(reindex(client, embed, lock["dimensions"])), flush=True)


if __name__ == "__main__":
    main()
