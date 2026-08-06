"""MemoryStore behaviour, against in-process Qdrant with stubbed encoders.

No server, no model download: the vectors are deterministic stand-ins so the
tests pin the *logic* — tenant isolation, scoring, dedup, deprecation — rather
than the embedding quality, which is measured separately.
"""
import math

import pytest

from app.services.memory_store import (
    GLOBAL_TENANT,
    KIND_ANALYSIS,
    KIND_PLAYBOOK,
    MemoryStore,
    playbook_point_id,
)


class StubEmbedder:
    """Maps text to a vector by hashing tokens into 8 dims, then L2-normalises,
    so 'similar text' really does give a higher cosine."""
    dim = 8

    def _vec(self, text: str):
        v = [0.0] * self.dim
        for tok in (text or "x").lower().split():
            v[hash(tok) % self.dim] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_query(self, text):
        return self._vec(text)

    def embed_passage(self, text):
        return self._vec(text)

    def embed_passages(self, texts):
        return [self._vec(t) for t in texts]


@pytest.fixture
def store(monkeypatch):
    s = MemoryStore(
        url=":memory:", collection="test_mem", embedder=StubEmbedder(),
        min_score=0.0,          # scoring/filtering logic is tested explicitly
        dedup_threshold=0.95,
    )
    # BM25 would need a model download; a constant sparse vector keeps the
    # hybrid query path exercised without it.
    from qdrant_client import models
    monkeypatch.setattr(
        s, "_sparse_vector",
        lambda text, is_query=False, error_codes=None: models.SparseVector(
            indices=[1, 2], values=[0.5, 0.5]
        ),
    )
    s.ensure_collection()
    return s


def _add(store, host="h1", tenant="acme", text="mysql deadlock lock timeout", **kw):
    return store.upsert_analysis(
        symptom_text=text, tenant_id=tenant, host=host, result_id="1",
        service=kw.pop("service", "mysql"), **kw,
    )


# ── tenant isolation ────────────────────────────────────────────────────────
def test_tenant_id_is_required_positionally():
    """Omitting it must fail at the call site, not silently read another
    tenant's data."""
    with pytest.raises(TypeError):
        MemoryStore.search(object(), "query")   # type: ignore[call-arg]


def test_other_tenants_analyses_are_invisible(store):
    _add(store, tenant="acme", text="mysql deadlock lock timeout")
    hits = store.search("mysql deadlock lock timeout", tenant_id="other-corp")
    assert hits == []


def test_own_tenant_sees_its_own(store):
    _add(store, tenant="acme", text="mysql deadlock lock timeout")
    assert len(store.search("mysql deadlock lock timeout", tenant_id="acme")) == 1


def test_global_playbooks_are_visible_to_every_tenant(store):
    store.upsert_playbooks([_playbook()], "v1")
    for tenant in ("acme", "other-corp"):
        hits = store.search("mysql lock wait timeout", tenant_id=tenant)
        assert [h.kind for h in hits] == [KIND_PLAYBOOK]


def _playbook(pid="mysql.lock_wait", title="InnoDB lock wait timeout"):
    return {
        "id": pid, "engine": "mysql", "frame": "Database", "severity": "high",
        "title": title, "symptom_text": "mysql lock wait timeout exceeded",
        "root_cause_chain": ["transaction held a row lock too long"],
        "fix_steps": ["SHOW ENGINE INNODB STATUS"],
        "verify_steps": ["check innodb_trx"], "error_codes": ["1205"],
        "docs_url": "https://example.invalid/doc",
    }


# ── deprecation ─────────────────────────────────────────────────────────────
def test_deprecated_points_never_come_back(store):
    pid = _add(store, text="mysql deadlock lock timeout")
    assert store.search("mysql deadlock lock timeout", tenant_id="acme")
    store.deprecate(pid)
    assert store.search("mysql deadlock lock timeout", tenant_id="acme") == []


# ── dedup ───────────────────────────────────────────────────────────────────
def test_identical_symptom_increments_instead_of_duplicating(store):
    text = "mysql deadlock lock timeout"
    first = _add(store, text=text)
    for _ in range(2):
        _add(store, text=text)
    assert store.client.count("test_mem").count == 1
    assert store.get(first).payload["occurrence_count"] == 3


def test_dedup_is_scoped_to_host(store):
    text = "mysql deadlock lock timeout"
    _add(store, host="h1", text=text)
    _add(store, host="h2", text=text)
    assert store.client.count("test_mem").count == 2


def test_empty_symptom_text_is_not_stored(store):
    assert _add(store, text="") is None
    assert store.client.count("test_mem").count == 0


# ── scoring ─────────────────────────────────────────────────────────────────
def test_verified_outranks_unverified_at_equal_relevance(store):
    payload_unverified = {"kind": KIND_ANALYSIS, "verified": False, "created_at": None}
    payload_verified = {"kind": KIND_ANALYSIS, "verified": True, "created_at": None}
    assert store._final_score(1.0, payload_verified) > store._final_score(1.0, payload_unverified)


def test_playbook_ranks_below_verified_analysis(store):
    """Shipped knowledge supplements a confirmed local case, never replaces it."""
    verified = store._final_score(1.0, {"kind": KIND_ANALYSIS, "verified": True})
    playbook = store._final_score(1.0, {"kind": KIND_PLAYBOOK})
    unverified = store._final_score(1.0, {"kind": KIND_ANALYSIS, "verified": False})
    assert unverified < playbook < verified


def test_older_analyses_decay(store):
    from datetime import datetime, timedelta, timezone
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    base = {"kind": KIND_ANALYSIS, "verified": True}
    assert store._final_score(1.0, {**base, "created_at": old}) < \
           store._final_score(1.0, {**base, "created_at": recent})


def test_playbooks_do_not_decay(store):
    """Documentation doesn't get stale by the day the way an incident does."""
    from datetime import datetime, timedelta, timezone
    old = (datetime.now(timezone.utc) - timedelta(days=3650)).isoformat()
    assert store._final_score(1.0, {"kind": KIND_PLAYBOOK, "created_at": old}) == \
           store._final_score(1.0, {"kind": KIND_PLAYBOOK})


def test_repeat_occurrences_raise_the_score(store):
    base = {"kind": KIND_ANALYSIS, "verified": False}
    assert store._final_score(1.0, {**base, "occurrence_count": 8}) > \
           store._final_score(1.0, {**base, "occurrence_count": 1})


def test_occurrence_boost_is_capped(store):
    base = {"kind": KIND_ANALYSIS, "verified": False}
    assert store._final_score(1.0, {**base, "occurrence_count": 10}) == \
           store._final_score(1.0, {**base, "occurrence_count": 9999})


def test_min_score_gates_on_similarity_not_final_score(store):
    """A verified hit gets a 1.6x multiplier. If the gate read final_score, a
    barely-relevant case would sail through on that multiplier alone."""
    store.min_score = 0.99
    _add(store, text="completely unrelated words here")
    assert store.search("mysql deadlock lock timeout", tenant_id="acme") == []


# ── slot reservation ────────────────────────────────────────────────────────
def test_a_real_case_keeps_a_slot_when_playbooks_would_sweep(store):
    from app.models.response import MemoryHit

    def hit(kind, score):
        return MemoryHit(point_id=kind + str(score), kind=kind, similarity=0.9,
                         final_score=score, symptom_text="s")

    hits = [hit(KIND_PLAYBOOK, 3), hit(KIND_PLAYBOOK, 2), hit(KIND_PLAYBOOK, 1.5),
            hit(KIND_ANALYSIS, 0.4)]
    kept = store._apply_slot_reservation(hits, limit=3)
    assert len(kept) == 3
    assert any(h.kind == KIND_ANALYSIS for h in kept)


def test_reservation_is_a_noop_when_a_case_already_made_the_cut(store):
    from app.models.response import MemoryHit

    hits = [MemoryHit(point_id="a", kind=KIND_ANALYSIS, similarity=0.9, final_score=3, symptom_text="s"),
            MemoryHit(point_id="b", kind=KIND_PLAYBOOK, similarity=0.9, final_score=2, symptom_text="s"),
            MemoryHit(point_id="c", kind=KIND_PLAYBOOK, similarity=0.9, final_score=1, symptom_text="s"),
            MemoryHit(point_id="d", kind=KIND_ANALYSIS, similarity=0.9, final_score=0.5, symptom_text="s")]
    assert [h.point_id for h in store._apply_slot_reservation(hits, 3)] == ["a", "b", "c"]


# ── feedback ────────────────────────────────────────────────────────────────
def test_correct_verdict_marks_verified_without_rewriting(store):
    pid = _add(store, root_cause_chain=["original cause"], fix_steps=["original fix"])
    store.mark_verified(pid, {"verdict": "correct", "resolved_by": "sumate"})
    p = store.get(pid).payload
    assert p["verified"] is True
    assert p["root_cause_chain"] == ["original cause"]


def test_wrong_verdict_overwrites_with_the_truth(store):
    pid = _add(store, root_cause_chain=["wrong guess"], fix_steps=["wrong fix"])
    store.mark_verified(pid, {
        "verdict": "wrong",
        "actual_root_cause": "it was the disk",
        "actual_fix": "replaced the disk",
    })
    p = store.get(pid).payload
    assert p["verified"] is True
    assert p["root_cause_chain"] == ["it was the disk"]
    assert p["fix_steps"] == ["replaced the disk"]


def test_feedback_does_not_touch_the_vector(store):
    """The symptom is still the symptom — what was wrong is the answer, not the
    question. Re-embedding on feedback would make the case unfindable."""
    pid = _add(store, text="mysql deadlock lock timeout")
    before = store.client.retrieve("test_mem", ids=[pid], with_vectors=True)[0].vector["dense"]
    store.mark_verified(pid, {"verdict": "wrong", "actual_root_cause": "x", "actual_fix": "y"})
    after = store.client.retrieve("test_mem", ids=[pid], with_vectors=True)[0].vector["dense"]
    assert before == after


# ── playbook seeding ────────────────────────────────────────────────────────
def test_playbook_ids_are_deterministic():
    assert playbook_point_id("mysql.foo") == playbook_point_id("mysql.foo")
    assert playbook_point_id("mysql.foo") != playbook_point_id("mysql.bar")


def test_reseeding_is_idempotent(store):
    entries = [_playbook()]
    store.upsert_playbooks(entries, "v1")
    store.upsert_playbooks(entries, "v2")
    assert store.client.count("test_mem").count == 1


def test_playbooks_are_never_marked_verified(store):
    """'verified' means a human confirmed this case happened here. Shipped
    knowledge can't satisfy that, and letting it claim so would inflate
    confidence on the strength of documentation."""
    store.upsert_playbooks([_playbook()], "v1")
    pt = store.get(playbook_point_id("mysql.lock_wait"))
    assert pt.payload["verified"] is False
    assert pt.payload["tenant_id"] == GLOBAL_TENANT


# ── failure handling ────────────────────────────────────────────────────────
def test_search_survives_a_broken_backend(store, monkeypatch):
    """Memory is an enhancement; a dead Qdrant must degrade, not 500."""
    monkeypatch.setattr(store, "_client", None)
    monkeypatch.setattr(
        MemoryStore, "client",
        property(lambda self: (_ for _ in ()).throw(RuntimeError("connection refused"))),
    )
    with pytest.raises(RuntimeError):
        _ = store.client            # the fixture itself is genuinely broken now
    # search() swallows it
    store2 = MemoryStore(url="http://127.0.0.1:1", collection="nope",
                         embedder=StubEmbedder(), timeout=0.2)
    assert store2.search("anything", tenant_id="acme") == []


def test_empty_query_returns_nothing(store):
    assert store.search("", tenant_id="acme") == []
