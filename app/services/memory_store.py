"""A4 memory — hybrid search over past analyses and shipped playbooks (Qdrant).

Three decisions worth knowing before changing anything here:

**Hybrid, not dense-only.** Logs are full of exact tokens that dense embeddings
are bad at — `ORA-01555`, `pos-cluster-01`, `errno 111`. Those are often the
single most identifying thing in a case, so a BM25 sparse vector runs alongside
the dense one and the two are fused with RRF.

**Two scores, never conflated.** `similarity` is the true cosine and is what
decides confidence downstream. `final_score` is that value weighted by
trust/recency/repetition and only ever decides ordering. RRF's own fused score
is a rank artefact (~0.016) and is meaningless as a similarity, so it is used
for recall and then discarded — the real cosine is recomputed from the stored
vectors, which is exact because they are L2-normalised.

**tenant_id is required, positionally.** Forgetting it must be a TypeError at
the call site, not a silent cross-tenant read. The only shared bucket is
`__global__`, which holds shipped playbooks and is read-only in practice.
"""
from __future__ import annotations

import logging
import math
import threading
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

GLOBAL_TENANT = "__global__"
DENSE = "dense"
SPARSE = "sparse"

KIND_ANALYSIS = "analysis"
KIND_PLAYBOOK = "playbook"

# Stable namespace so re-seeding a playbook overwrites its point instead of
# creating a second copy of the same advice.
_PLAYBOOK_NS = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


def playbook_point_id(entry_id: str) -> str:
    return str(uuid.uuid5(_PLAYBOOK_NS, entry_id))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _days_since(iso: str | None) -> int:
    if not iso:
        return 0
    try:
        then = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return 0
    return max(0, (datetime.now(timezone.utc) - then).days)


class MemoryStore:
    def __init__(
        self,
        url: str,
        collection: str,
        embedder,
        sparse_model: str = "Qdrant/bm25",
        time_decay_days: int = 180,
        dedup_threshold: float = 0.95,
        min_score: float = 0.80,
        playbook_weight: float = 1.2,
        reserve_analysis_slots: int = 1,
        timeout: float = 2.0,
    ):
        self.url = url
        self.collection = collection
        self.embedder = embedder
        self.sparse_model_name = sparse_model
        self.time_decay_days = time_decay_days
        self.dedup_threshold = dedup_threshold
        self.min_score = min_score
        self.playbook_weight = playbook_weight
        self.reserve_analysis_slots = reserve_analysis_slots
        self.timeout = timeout

        self._client = None
        self._sparse = None
        self._lock = threading.Lock()

    # ── lazy resources ──────────────────────────────────────────────────────
    @property
    def client(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    from qdrant_client import QdrantClient
                    # ":memory:" runs Qdrant in-process — used by the unit tests
                    # so they need no server.
                    self._client = (
                        QdrantClient(":memory:") if self.url == ":memory:"
                        else QdrantClient(url=self.url, timeout=self.timeout)
                    )
        return self._client

    @property
    def sparse(self):
        """BM25 from fastembed. Pinned by name on purpose — Qdrant does not
        generate sparse vectors itself, and a different implementation changes
        which tokens survive, which is what makes an exact-code search hit or
        miss."""
        if self._sparse is None:
            with self._lock:
                if self._sparse is None:
                    from fastembed import SparseTextEmbedding
                    self._sparse = SparseTextEmbedding(model_name=self.sparse_model_name)
        return self._sparse

    def _sparse_vector(self, text: str, is_query: bool = False, error_codes: list[str] | None = None):
        from qdrant_client import models

        if error_codes:
            # Repeat the codes so BM25 weights them above the surrounding prose.
            # Without this, a bare-code query like "40P01" loses: dense
            # similarity is near-noise for a lone token (~0.81 against
            # everything), and plain RRF lets that noise outvote the one exact
            # sparse match. This is what the separate error_codes field is for.
            text = f"{text} {' '.join(error_codes)} {' '.join(error_codes)}"
        gen = self.sparse.query_embed(text) if is_query else self.sparse.embed([text])
        emb = next(iter(gen))
        return models.SparseVector(indices=emb.indices.tolist(), values=emb.values.tolist())

    # ── schema ──────────────────────────────────────────────────────────────
    def ensure_collection(self) -> None:
        from qdrant_client import models

        if self.client.collection_exists(self.collection):
            return
        self.client.create_collection(
            collection_name=self.collection,
            vectors_config={
                DENSE: models.VectorParams(
                    size=self.embedder.dim, distance=models.Distance.COSINE
                )
            },
            # IDF modifier is required for BM25 to score properly — without it
            # every term is weighted equally and rare error codes stop standing out.
            sparse_vectors_config={
                SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)
            },
        )
        for field, schema in [
            ("tenant_id", models.PayloadSchemaType.KEYWORD),
            ("host", models.PayloadSchemaType.KEYWORD),
            ("service", models.PayloadSchemaType.KEYWORD),
            ("frame", models.PayloadSchemaType.KEYWORD),
            ("kind", models.PayloadSchemaType.KEYWORD),
            ("verified", models.PayloadSchemaType.BOOL),
            ("deprecated", models.PayloadSchemaType.BOOL),
            ("created_at", models.PayloadSchemaType.KEYWORD),
        ]:
            self.client.create_payload_index(self.collection, field, schema)
        logger.info("Created Qdrant collection %s", self.collection)

    def health(self) -> bool:
        try:
            self.client.get_collections()
            return True
        except Exception as exc:
            logger.warning("Qdrant health check failed: %s", exc)
            return False

    # ── filters ─────────────────────────────────────────────────────────────
    def _build_filter(self, tenant_id: str, host=None, service=None, frame=None, kind=None):
        from qdrant_client import models

        must = [
            # Tenant isolation, plus the shared playbook bucket. Anything else
            # is another customer's data.
            models.FieldCondition(
                key="tenant_id",
                match=models.MatchAny(any=[tenant_id, GLOBAL_TENANT]),
            )
        ]
        # Deprecated points are cases an operator has explicitly retired; they
        # must never come back, however well they score.
        must_not = [models.FieldCondition(key="deprecated", match=models.MatchValue(value=True))]

        if host:
            must.append(models.FieldCondition(key="host", match=models.MatchValue(value=host)))
        if frame:
            must.append(models.FieldCondition(key="frame", match=models.MatchValue(value=frame)))
        if kind:
            must.append(models.FieldCondition(key="kind", match=models.MatchValue(value=kind)))
        if service:
            # Playbooks live under __global__ and are matched by service too, so
            # a service filter must not exclude them for the wrong host.
            must.append(models.FieldCondition(key="service", match=models.MatchValue(value=service)))
        return models.Filter(must=must, must_not=must_not)

    # ── scoring ─────────────────────────────────────────────────────────────
    def _final_score(self, relevance: float, payload: dict) -> float:
        """Weight a relevance score for ordering.

        `relevance` is the *fused* score, not the raw cosine. Basing this on
        cosine alone would silently throw the sparse half away: re-sorting by a
        cosine-derived value undoes the fusion entirely, and a bare-code query
        like "40P01" then loses to whatever the dense vector happened to like.
        Confidence still reads `similarity`, which stays the true cosine.
        """
        kind = payload.get("kind", KIND_ANALYSIS)
        if kind == KIND_PLAYBOOK:
            # Deliberately below a verified analysis (1.6): a confirmed case on
            # our own machine always outranks general engine knowledge. And no
            # time decay — documentation doesn't get stale by the day the way a
            # specific incident does.
            return relevance * self.playbook_weight

        weight = 1.6 if payload.get("verified") else 1.0
        decay = math.exp(-_days_since(payload.get("created_at")) / self.time_decay_days)
        occurrences = 1 + 0.05 * min(int(payload.get("occurrence_count") or 0), 10)
        return relevance * weight * decay * occurrences

    def _to_hit(self, point, query_vec: list[float], max_fused: float = 1.0):
        from app.models.response import MemoryHit

        payload = point.payload or {}
        # Recompute the true cosine: RRF's fused score is a rank artefact, and
        # the stored vectors are unit-length so a dot product is exactly cosine.
        vec = point.vector.get(DENSE) if isinstance(point.vector, dict) else point.vector
        similarity = float(sum(a * b for a, b in zip(query_vec, vec))) if vec else 0.0
        similarity = max(-1.0, min(1.0, similarity))

        # Fused scores are only meaningful relative to each other within one
        # query, so they're normalised to put the best candidate at 1.0. That
        # keeps final_score readable (and roughly comparable to the spec's
        # similarity-based scale) without changing any ordering.
        relevance = float(getattr(point, "score", 0.0) or 0.0) / (max_fused or 1.0)

        return MemoryHit(
            point_id=str(point.id),
            kind=payload.get("kind", KIND_ANALYSIS),
            similarity=round(similarity, 4),
            final_score=round(self._final_score(relevance, payload), 4),
            symptom_text=payload.get("symptom_text", ""),
            root_cause_chain=payload.get("root_cause_chain") or [],
            fix_steps=payload.get("fix_steps") or [],
            verified=bool(payload.get("verified")),
            actual_fix=payload.get("actual_fix"),
            occurrence_count=int(payload.get("occurrence_count") or 0),
            created_at=payload.get("created_at", ""),
            days_ago=_days_since(payload.get("created_at")),
            title=payload.get("title"),
            verify_steps=payload.get("verify_steps") or [],
            docs_url=payload.get("docs_url"),
        )

    # ── search ──────────────────────────────────────────────────────────────
    def search(
        self,
        query_text: str,
        tenant_id: str,
        host: str | None = None,
        service: str | None = None,
        frame: str | None = None,
        limit: int = 3,
        prefer_verified: bool = True,
        exclude_ids: set[str] | None = None,
    ) -> list:
        """Hybrid search. `tenant_id` has no default — omitting it is a
        TypeError at the call site rather than a silent cross-tenant read.

        `exclude_ids` drops points this tenant has retired. Shared playbooks
        can't carry a `deprecated` flag of their own — one tenant setting it
        would hide the entry from everyone, and re-seeding would undo it — so
        that state lives per-tenant in SQLite and arrives here as a filter.
        """
        from qdrant_client import models

        if not query_text:
            return []

        query_vec = self.embedder.embed_query(query_text)
        flt = self._build_filter(tenant_id, host=host, service=service, frame=frame)
        # Over-fetch: filtering by min_score and reserving slots both discard
        # candidates, so the fused list has to be longer than the final answer.
        candidates = max(limit * 4, 12)

        try:
            result = self.client.query_points(
                collection_name=self.collection,
                prefetch=[
                    models.Prefetch(query=query_vec, using=DENSE, limit=candidates, filter=flt),
                    models.Prefetch(
                        query=self._sparse_vector(query_text, is_query=True),
                        using=SPARSE, limit=candidates, filter=flt,
                    ),
                ],
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                limit=candidates,
                with_payload=True,
                with_vectors=True,
            )
        except Exception as exc:
            logger.warning("Memory search failed: %s", exc)
            return []

        points = result.points
        if exclude_ids:
            points = [p for p in points if str(p.id) not in exclude_ids]
        max_fused = max((float(p.score or 0.0) for p in points), default=1.0)
        hits = [self._to_hit(p, query_vec, max_fused) for p in points]
        # min_score gates on raw similarity, never on final_score — otherwise a
        # verified but barely-relevant case sails through on its 1.6 multiplier.
        hits = [h for h in hits if h.similarity >= self.min_score]
        hits.sort(key=lambda h: h.final_score, reverse=True)

        return self._apply_slot_reservation(hits, limit)

    def _apply_slot_reservation(self, hits: list, limit: int) -> list:
        """Keep at least one slot for a real case when playbooks would sweep
        the lot — shipped advice should supplement our own history, not bury it."""
        reserve = self.reserve_analysis_slots
        if reserve <= 0 or len(hits) <= limit:
            return hits[:limit]

        chosen = hits[:limit]
        if any(h.kind == KIND_ANALYSIS for h in chosen):
            return chosen

        best_analysis = next((h for h in hits if h.kind == KIND_ANALYSIS), None)
        if best_analysis is None:
            return chosen
        return chosen[: limit - 1] + [best_analysis]

    # ── writes ──────────────────────────────────────────────────────────────
    def _find_duplicate(self, passage_vec: list[float], tenant_id: str, host: str, service: str | None):
        """Dedup compares passage-vs-passage, where identical text scores 1.0.
        The query-vs-passage scale used by search() tops out around 0.96, so
        reusing it here would make dedup_threshold=0.95 nearly unreachable."""
        from qdrant_client import models

        flt = self._build_filter(tenant_id, host=host, service=service, kind=KIND_ANALYSIS)
        try:
            res = self.client.query_points(
                collection_name=self.collection,
                query=passage_vec, using=DENSE, limit=1,
                query_filter=flt, with_payload=True,
            )
        except Exception as exc:
            logger.warning("Dedup lookup failed: %s", exc)
            return None
        if res.points and res.points[0].score >= self.dedup_threshold:
            return res.points[0]
        return None

    def upsert_analysis(
        self,
        symptom_text: str,
        tenant_id: str,
        host: str,
        result_id: str,
        service: str | None = None,
        frame: str | None = None,
        severity: str | None = None,
        root_cause_chain: list[str] | None = None,
        fix_steps: list[str] | None = None,
        confidence: float = 0.0,
        pre_fix: bool = False,
    ) -> str | None:
        from qdrant_client import models

        if not symptom_text:
            return None

        vec = self.embedder.embed_passage(symptom_text)

        existing = self._find_duplicate(vec, tenant_id, host, service)
        if existing:
            count = int((existing.payload or {}).get("occurrence_count") or 1) + 1
            self.client.set_payload(
                collection_name=self.collection,
                payload={"occurrence_count": count, "last_seen_at": _now_iso()},
                points=[existing.id],
            )
            logger.info("Memory dedup — host=%s occurrence_count=%d", host, count)
            return str(existing.id)

        point_id = str(uuid.uuid4())
        payload = {
            "kind": KIND_ANALYSIS,
            "tenant_id": tenant_id,
            "host": host,
            "service": service,
            "frame": frame,
            "severity": severity,
            "symptom_text": symptom_text,
            "root_cause_chain": root_cause_chain or [],
            "fix_steps": fix_steps or [],
            "confidence": confidence,
            "verified": False,
            "feedback_verdict": None,
            "actual_root_cause": None,
            "actual_fix": None,
            "result_id": result_id,
            "created_at": _now_iso(),
            "occurrence_count": 1,
            "deprecated": False,
            # Marks data captured before the Phase 1.5 health_score fix so it can
            # be told apart (or dropped) later.
            "pre_fix": pre_fix,
        }
        self.client.upsert(
            collection_name=self.collection,
            points=[models.PointStruct(
                id=point_id,
                vector={DENSE: vec, SPARSE: self._sparse_vector(symptom_text)},
                payload=payload,
            )],
        )
        return point_id

    def upsert_playbooks(self, entries: list[dict], version: str) -> int:
        """Seed shipped playbooks. Point ids are derived from the entry id, so
        re-seeding replaces in place and the script stays idempotent."""
        from qdrant_client import models

        if not entries:
            return 0
        texts = [e["symptom_text"] for e in entries]
        vectors = self.embedder.embed_passages(texts)

        points = []
        for entry, vec in zip(entries, vectors):
            points.append(models.PointStruct(
                id=playbook_point_id(entry["id"]),
                vector={
                    DENSE: vec,
                    SPARSE: self._sparse_vector(
                        entry["symptom_text"], error_codes=entry.get("error_codes")
                    ),
                },
                payload={
                    "kind": KIND_PLAYBOOK,
                    "playbook_id": entry["id"],
                    "tenant_id": GLOBAL_TENANT,
                    "host": None,
                    "service": entry["engine"],
                    "frame": entry["frame"],
                    "severity": entry["severity"],
                    "title": entry["title"],
                    "symptom_text": entry["symptom_text"],
                    "root_cause_chain": entry["root_cause_chain"],
                    "fix_steps": entry["fix_steps"],
                    "verify_steps": entry["verify_steps"],
                    "error_codes": entry.get("error_codes") or [],
                    "docs_url": entry["docs_url"],
                    "applies_to": entry.get("applies_to"),
                    "playbook_version": version,
                    # Never "verified": that word means a human confirmed this
                    # exact case happened here, which is not what a playbook is.
                    "verified": False,
                    "occurrence_count": 0,
                    "deprecated": False,
                    "created_at": _now_iso(),
                },
            ))
        self.client.upsert(collection_name=self.collection, points=points)
        return len(points)

    def mark_verified(self, point_id: str, feedback: dict) -> None:
        """Record an operator's verdict. Never touches the vector: the symptom
        is still the symptom — what was wrong is the answer, not the question."""
        payload = {
            "verified": True,
            "feedback_verdict": feedback.get("verdict"),
            "actual_root_cause": feedback.get("actual_root_cause"),
            "actual_fix": feedback.get("actual_fix"),
            "resolved_by": feedback.get("resolved_by"),
            "verified_at": _now_iso(),
        }
        if feedback.get("verdict") == "wrong":
            if feedback.get("actual_root_cause"):
                payload["root_cause_chain"] = [feedback["actual_root_cause"]]
            if feedback.get("actual_fix"):
                payload["fix_steps"] = [feedback["actual_fix"]]
        self.client.set_payload(
            collection_name=self.collection,
            payload={k: v for k, v in payload.items() if v is not None},
            points=[point_id],
        )

    def deprecate(self, point_id: str) -> None:
        self.client.set_payload(
            collection_name=self.collection,
            payload={"deprecated": True, "deprecated_at": _now_iso()},
            points=[point_id],
        )

    def warm_up(self) -> None:
        """Load the BM25 encoder ahead of the first search.

        Warming only the dense embedder is not enough: fastembed loads its model
        lazily too, and that load runs *inside* the 2s search timeout. The first
        real A4 query then always times out — so the very first analysis after a
        restart is the one case that silently gets no memory at all.
        """
        try:
            self._sparse_vector("warmup", is_query=True)
            logger.info("Memory sparse encoder warm-up done")
        except Exception as exc:
            logger.error("Memory warm-up failed — A4 will degrade: %s", exc)

    def get(self, point_id: str):
        pts = self.client.retrieve(self.collection, ids=[point_id], with_payload=True)
        return pts[0] if pts else None


# ── module singleton ────────────────────────────────────────────────────────
_store: MemoryStore | None = None
_store_lock = threading.Lock()


def get_store() -> MemoryStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                from app.config import config
                from app.services.embedder import get_embedder

                m = config.memory
                _store = MemoryStore(
                    url=m.qdrant_url,
                    collection=m.collection,
                    embedder=get_embedder(),
                    sparse_model=m.sparse_model,
                    time_decay_days=m.time_decay_days,
                    dedup_threshold=m.dedup_threshold,
                    min_score=m.min_score,
                    playbook_weight=m.playbook_pack.kind_weight,
                    reserve_analysis_slots=m.playbook_pack.reserve_analysis_slots,
                    timeout=m.timeout_seconds,
                )
    return _store
