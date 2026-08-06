"""Embedder contract.

Runs against a stub model — the real one is ~470MB and would make the unit
suite depend on a network fetch. What matters here is the wiring around the
model (prefixes, normalisation, batching, failure handling), not the weights.
"""
import numpy as np
import pytest

from app.services import embedder as embedder_mod
from app.services.embedder import DIM, PASSAGE_PREFIX, QUERY_PREFIX, Embedder


class StubModel:
    """Records what it was asked to encode and returns deterministic vectors."""

    def __init__(self, dim=DIM):
        self.dim = dim
        self.seen: list[list[str]] = []

    def encode(self, texts, normalize_embeddings=False, convert_to_numpy=True, show_progress_bar=False):
        self.seen.append(list(texts))
        # length-varying vectors so we can tell whether normalisation happened
        out = np.array([[float(len(t) + i) for i in range(self.dim)] for t in texts])
        if normalize_embeddings:
            out = out / np.linalg.norm(out, axis=1, keepdims=True)
        return out


@pytest.fixture
def emb(monkeypatch):
    e = Embedder()
    stub = StubModel()
    monkeypatch.setattr(e, "_ensure_model", lambda: stub)
    e._model = stub
    return e, stub


# ── e5 prefixes ─────────────────────────────────────────────────────────────
def test_query_gets_query_prefix(emb):
    e, stub = emb
    e.embed_query("mysql deadlock")
    assert stub.seen[-1] == [QUERY_PREFIX + "mysql deadlock"]


def test_passages_get_passage_prefix(emb):
    e, stub = emb
    e.embed_passages(["a", "b"])
    assert stub.seen[-1] == [PASSAGE_PREFIX + "a", PASSAGE_PREFIX + "b"]


def test_query_and_passage_prefixes_differ(emb):
    """e5 is asymmetric: a query embedded as a passage doesn't match properly.
    Using one prefix for both is a silent quality loss, so it's pinned here."""
    assert QUERY_PREFIX != PASSAGE_PREFIX
    e, stub = emb
    e.embed_query("x")
    e.embed_passage("x")
    assert stub.seen[-2] != stub.seen[-1]


# ── vector contract ─────────────────────────────────────────────────────────
def test_dim_is_384(emb):
    e, _ = emb
    assert e.dim == DIM == 384
    assert len(e.embed_query("x")) == 384


def test_vectors_are_l2_normalised(emb):
    """Qdrant cosine only behaves as expected on unit vectors."""
    e, _ = emb
    for vec in [e.embed_query("hello")] + e.embed_passages(["a", "bb", "ccc"]):
        assert np.isclose(np.linalg.norm(vec), 1.0), np.linalg.norm(vec)


def test_returns_plain_lists_not_numpy(emb):
    """Qdrant's client wants JSON-serialisable values."""
    e, _ = emb
    v = e.embed_query("x")
    assert isinstance(v, list) and isinstance(v[0], float)


# ── batching ────────────────────────────────────────────────────────────────
def test_batch_is_one_encode_call(emb):
    e, stub = emb
    before = len(stub.seen)
    e.embed_passages(["a", "b", "c", "d"])
    assert len(stub.seen) == before + 1     # not four


def test_empty_batch_short_circuits(emb):
    e, stub = emb
    before = len(stub.seen)
    assert e.embed_passages([]) == []
    assert len(stub.seen) == before          # model never touched


def test_empty_string_is_tolerated(emb):
    e, _ = emb
    assert len(e.embed_query("")) == DIM


# ── loading behaviour ───────────────────────────────────────────────────────
def test_model_is_not_loaded_at_construction(monkeypatch):
    """Importing torch costs seconds; nothing should pay it until first use."""
    e = Embedder()
    assert e._model is None
    assert not e.ready


def test_warm_up_failure_degrades_instead_of_raising(monkeypatch):
    """A broken embedder must not stop the app from booting — memory degrades
    to unavailable and the rest of the pipeline carries on."""
    e = Embedder()

    def boom():
        raise RuntimeError("no model for you")

    monkeypatch.setattr(e, "_ensure_model", boom)
    e.warm_up()          # must not raise
    assert e.failed is True
    assert e.ready is False


def test_singleton_is_reused(monkeypatch):
    monkeypatch.setattr(embedder_mod, "_embedder", None)
    a = embedder_mod.get_embedder()
    b = embedder_mod.get_embedder()
    assert a is b
