"""Embedding service — local sentence-transformers, no Ollama, no network at call time.

Runs `intfloat/multilingual-e5-small` (384 dims, ~118M params) on CPU. Small
enough to sit in the request path, multilingual so Thai and English symptom text
land in the same space.

Two things about e5 that are easy to get wrong and expensive to debug:

1. **Prefixes are mandatory.** The model was trained with "query: " on searches
   and "passage: " on indexed text. Drop them and retrieval quality falls off
   noticeably — with no error, no warning, just quietly worse results. They are
   applied here so no caller has to remember.

2. **Asymmetry matters.** A query embedded with the passage prefix does not
   match its own passage properly, so the two entry points are kept separate
   rather than offering one generic `embed()`.
"""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

MODEL_NAME = "intfloat/multilingual-e5-small"
DIM = 384

QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "


class Embedder:
    """Lazy-loaded, thread-safe wrapper around one sentence-transformers model."""

    def __init__(self, model_name: str = MODEL_NAME, device: str = "cpu", dim: int = DIM):
        self.model_name = model_name
        self.device = device
        self._dim = dim
        self._model = None
        self._lock = threading.Lock()
        self._failed = False

    # ── loading ─────────────────────────────────────────────────────────────
    def _ensure_model(self):
        """Load on first use. Importing sentence-transformers pulls in torch,
        which costs seconds — doing it at module import would slow every process
        that touches this file, including the test suite and one-off scripts."""
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:      # another thread won the race
                return self._model
            from sentence_transformers import SentenceTransformer  # local: heavy import

            t0 = time.monotonic()
            model = SentenceTransformer(self.model_name, device=self.device)
            self._model = model
            logger.info(
                "Embedder loaded %s on %s in %.1fs", self.model_name, self.device,
                time.monotonic() - t0,
            )
            return model

    def warm_up(self) -> None:
        """Load the model ahead of the first real request.

        Called as a background task at startup: the first load takes several
        seconds, and paying that inside a request would blow the latency budget
        outright. Best-effort — a failure here degrades memory to "unavailable",
        it must never stop the app from booting.
        """
        try:
            self._ensure_model()
            self.embed_query("warmup")   # first forward pass allocates buffers too
            logger.info("Embedder warm-up done")
        except Exception as exc:
            self._failed = True
            logger.error("Embedder warm-up failed — memory will degrade: %s", exc)

    @property
    def ready(self) -> bool:
        return self._model is not None

    @property
    def failed(self) -> bool:
        return self._failed

    @property
    def dim(self) -> int:
        return self._dim

    # ── embedding ───────────────────────────────────────────────────────────
    def _encode(self, texts: list[str]) -> list[list[float]]:
        model = self._ensure_model()
        # normalize_embeddings gives unit vectors, which is what makes Qdrant's
        # cosine distance equivalent to a dot product — and keeps similarity
        # scores comparable across points.
        vectors = model.encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [v.tolist() for v in vectors]

    def embed_query(self, text: str) -> list[float]:
        """Embed a search string (gets the "query: " prefix)."""
        return self._encode([QUERY_PREFIX + (text or "")])[0]

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        """Embed text being stored (gets the "passage: " prefix).

        Batched deliberately: one encode() over N texts is far cheaper than N
        calls, and indexing always has more than one.
        """
        if not texts:
            return []
        return self._encode([PASSAGE_PREFIX + (t or "") for t in texts])

    def embed_passage(self, text: str) -> list[float]:
        return self.embed_passages([text])[0]


# ── module-level singleton ──────────────────────────────────────────────────
# One model per process: it is a few hundred MB of weights, and every extra copy
# is that much RAM for no benefit.
_embedder: Embedder | None = None
_singleton_lock = threading.Lock()


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        with _singleton_lock:
            if _embedder is None:
                from app.config import config   # local import: avoid cycle

                _embedder = Embedder(
                    model_name=config.memory.embedding_model,
                    device=config.memory.embedding_device,
                )
    return _embedder


async def warm_up() -> None:
    """Startup hook — mirrors perplexica_client.warm_up()."""
    from app.config import config   # local import: avoid cycle

    if not (config.memory.enabled and config.memory.warm_up_on_startup):
        return
    import asyncio
    # to_thread: loading is CPU-bound and synchronous, and must not block the
    # event loop while the rest of the app finishes starting.
    await asyncio.to_thread(get_embedder().warm_up)
