"""Qdrant layout for knowledge chunks (FR-12 / KR-ADP-03: KR-D5, KR-D6).

- One shared public collection and one private collection per tenant: tenants are separated by collection, not
  by a payload filter. Collection names are built only from a validated tenant id, which callers take from the
  verified token, never from a request body.
- Anonymous (T0) principals search the public collection only; T1 / T2 add their own tenant's collection.
- Each point is a child chunk with a dense vector and a sparse BM25-style vector (term counts here, IDF
  applied by Qdrant), and a payload carrying its parent section, audience, provenance and the copied ACL.
- Copied ACLs filter locally (fast path); restricted documents still need a live check against the source
  system with the user's on-behalf-of token before use (KR-D6, done at retrieval).
"""

import re
import uuid
import zlib
from collections import Counter

from qdrant_client import models

TENANT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
PUBLIC = "public"
VERSION = "v1"
WORD = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")  # keeps error codes like e-1042 or v2.3.1 whole
# Every payload field a filter uses: Qdrant Cloud refuses filtered search on an unindexed field (B18).
KEYWORD, BOOL = models.PayloadSchemaType.KEYWORD, models.PayloadSchemaType.BOOL
PAYLOAD_INDEXES = {"doc_id": KEYWORD, "source_system": KEYWORD, "acl_groups": KEYWORD, "audience": KEYWORD,
                   "is_restricted": BOOL}


def collection(scope, namespace=""):
    """Collection for "public" or a tenant id."""
    if scope == PUBLIC:
        return f"{namespace}public_knowledge_{VERSION}"
    if not TENANT_ID.match(scope):
        raise ValueError(f"invalid tenant id: {scope!r}")
    return f"{namespace}tenant_{scope.lower()}_private_{VERSION}"


def collections_for(tier, tenant_id, namespace=""):
    """What a principal may search: T0 the public collection only, T1 / T2 also their own tenant's."""
    if tier == "T0_ANON":
        return [collection(PUBLIC, namespace)]
    return [collection(PUBLIC, namespace), collection(tenant_id, namespace)]


def sparse(text):
    """BM25-style sparse vector: hashed term counts; Qdrant applies IDF (Modifier.IDF)."""
    counts = Counter(zlib.crc32(term.encode()) for term in WORD.findall(text.lower()))
    return models.SparseVector(indices=list(counts), values=[float(n) for n in counts.values()])


def point_id(chunk_id):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


def doc_filter(doc_id):
    return models.Filter(must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))])


def visible(payload, groups):
    """Fast-path ACL check on copied metadata; restricted documents are never decided here."""
    if payload.get("is_restricted"):
        return False
    allowed = payload.get("acl_groups") or []
    return not allowed or bool(set(allowed) & set(groups))


class Index:
    def __init__(self, client, dimensions, namespace=""):
        self.client, self.dimensions, self.namespace = client, dimensions, namespace

    def ensure(self, scope):
        """The collection, created if missing, with every payload index; an older collection gains the missing
        ones, so a reindex repairs it."""
        name = collection(scope, self.namespace)
        if not self.client.collection_exists(name):
            self.client.create_collection(
                name,
                vectors_config={"dense": models.VectorParams(size=self.dimensions,
                                                             distance=models.Distance.COSINE)},
                sparse_vectors_config={"bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)})
        indexed = self.client.get_collection(name).payload_schema
        for field, schema in PAYLOAD_INDEXES.items():
            if field not in indexed:
                self.client.create_payload_index(name, field, schema, wait=True)
        return name

    def replace_document(self, scope, doc_id, chunks, vectors):
        """In-place re-index of one document: its old chunks go, the new ones come in."""
        name = self.ensure(scope)
        self.client.delete(name, points_selector=models.FilterSelector(filter=doc_filter(doc_id)), wait=True)
        points = [models.PointStruct(id=point_id(chunk["chunk_id"]),
                                     vector={"dense": vector, "bm25": sparse(chunk["text"])}, payload=chunk)
                  for chunk, vector in zip(chunks, vectors, strict=True)]
        if points:
            self.client.upsert(name, points=points, wait=True)

    def delete_document(self, scope, doc_id):
        name = collection(scope, self.namespace)
        if self.client.collection_exists(name):
            selector = models.FilterSelector(filter=doc_filter(doc_id))
            self.client.delete(name, points_selector=selector, wait=True)

    def count(self, scope, doc_id):
        name = collection(scope, self.namespace)
        if not self.client.collection_exists(name):
            return 0
        return self.client.count(name, count_filter=doc_filter(doc_id), exact=True).count

    def doc_ids(self, scope, source_system):
        """Every document id indexed from one source system."""
        name = collection(scope, self.namespace)
        if not self.client.collection_exists(name):
            return set()
        source = models.Filter(must=[models.FieldCondition(key="source_system",
                                                           match=models.MatchValue(value=source_system))])
        found, offset = set(), None
        while True:
            points, offset = self.client.scroll(name, scroll_filter=source, limit=256, offset=offset,
                                                with_payload=["doc_id"], with_vectors=False)
            found |= {point.payload["doc_id"] for point in points}
            if offset is None:
                return found

    def search(self, tier, tenant_id, vector, groups=(), limit=10):
        """Dense child-chunk hits the principal may see (fast-path ACL), best first. Hybrid ranking: M1-11."""
        open_to = [models.IsEmptyCondition(is_empty=models.PayloadField(key="acl_groups"))]
        if groups:
            open_to.append(models.FieldCondition(key="acl_groups", match=models.MatchAny(any=list(groups))))
        allowed = models.Filter(
            must=[models.Filter(should=open_to)],
            must_not=[models.FieldCondition(key="is_restricted", match=models.MatchValue(value=True))])
        hits = []
        for name in collections_for(tier, tenant_id, self.namespace):
            if self.client.collection_exists(name):
                hits += self.client.query_points(name, query=vector, using="dense", query_filter=allowed,
                                                 limit=limit, with_payload=True).points
        hits = [hit for hit in hits if visible(hit.payload, groups)]  # belt and braces
        return sorted(hits, key=lambda hit: -hit.score)[:limit]
