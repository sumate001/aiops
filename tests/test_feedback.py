"""Feedback endpoints.

The rules encoded here are what stop memory from becoming a record of its own
mistakes: a verdict is per-host, "wrong" must carry the correction, and shipped
playbooks can't be edited into tenant-specific forks.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import feedback_router
from app.services import feedback_store
from app.services.memory_store import KIND_ANALYSIS, KIND_PLAYBOOK


class FakePoint:
    def __init__(self, pid, payload):
        self.id = pid
        self.payload = payload


class FakeStore:
    def __init__(self):
        self.points = {}
        self.verified = {}
        self.deprecated = set()
        self.deleted = set()
        self.collection = "c"

    def get(self, pid):
        return self.points.get(pid)

    def mark_verified(self, pid, feedback):
        self.verified[pid] = feedback
        self.points[pid].payload["verified"] = True
        self.points[pid].payload["feedback_verdict"] = feedback.get("verdict")
        for k in ("actual_root_cause", "actual_fix", "resolved_by"):
            if feedback.get(k):
                self.points[pid].payload[k] = feedback[k]

    def deprecate(self, pid):
        self.deprecated.add(pid)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(feedback_store, "_DB_PATH", tmp_path / "t.db")
    feedback_store.init_feedback_tables()

    store = FakeStore()
    store.points["pt-analysis"] = FakePoint("pt-analysis", {
        "kind": KIND_ANALYSIS, "verified": False,
        "root_cause_chain": ["original"], "fix_steps": ["original fix"],
    })
    store.points["pt-playbook"] = FakePoint("pt-playbook", {
        "kind": KIND_PLAYBOOK, "verified": False, "title": "InnoDB lock wait",
    })
    monkeypatch.setattr(feedback_router, "_store", lambda: store)

    feedback_store.link_memory_point("42", "host-a", "acme", "pt-analysis")
    feedback_store.link_memory_point("42", "host-b", "acme", "pt-playbook")

    app = FastAPI()
    app.include_router(feedback_router.router)
    c = TestClient(app)
    c.store = store
    return c


def _post(client, host="host-a", **body):
    return client.post(f"/api/results/42/hosts/{host}/feedback", json=body)


# ── the three verdicts ──────────────────────────────────────────────────────
def test_correct_marks_verified_and_keeps_content(client):
    r = _post(client, verdict="correct", resolved_by="sumate")
    assert r.status_code == 200
    assert r.json()["memory_point_id"] == "pt-analysis"
    payload = client.store.points["pt-analysis"].payload
    assert payload["verified"] is True
    assert payload["root_cause_chain"] == ["original"]


def test_partial_adds_detail_without_replacing(client):
    r = _post(client, verdict="partial", actual_fix="also restarted the pool")
    assert r.status_code == 200
    payload = client.store.points["pt-analysis"].payload
    assert payload["verified"] is True
    assert payload["actual_fix"] == "also restarted the pool"
    assert payload["root_cause_chain"] == ["original"]     # not overwritten


def test_wrong_records_the_correction(client):
    r = _post(client, verdict="wrong",
              actual_root_cause="it was the disk", actual_fix="replaced the disk")
    assert r.status_code == 200
    assert client.store.verified["pt-analysis"]["actual_fix"] == "replaced the disk"


def test_wrong_without_the_correction_is_rejected(client):
    """A wrong answer that a human corrected is the most valuable data the
    system can get. Recording "we were wrong" and losing what was right makes
    the entry useless — worse, it stays verified."""
    r = _post(client, verdict="wrong")
    assert r.status_code == 400
    assert client.store.points["pt-analysis"].payload["verified"] is False


def test_unknown_verdict_is_rejected(client):
    assert _post(client, verdict="mostly-ok").status_code == 400


# ── per-host granularity ────────────────────────────────────────────────────
def test_feedback_is_scoped_to_one_host(client, monkeypatch):
    """One analysis covers several hosts and each has its own memory point;
    confirming one must not silently confirm the others."""
    other = FakePoint("pt-other", {"kind": KIND_ANALYSIS, "verified": False})
    client.store.points["pt-other"] = other
    feedback_store.link_memory_point("42", "host-c", "acme", "pt-other")

    _post(client, host="host-a", verdict="correct")
    assert client.store.points["pt-analysis"].payload["verified"] is True
    assert other.payload["verified"] is False


def test_unknown_result_or_host_is_404(client):
    assert _post(client, host="nope", verdict="correct").status_code == 404


# ── playbooks are not editable ──────────────────────────────────────────────
def test_feedback_on_a_playbook_is_409(client):
    """Playbooks are shared under __global__: an edit here would change them for
    every tenant, and the next seed run would overwrite it anyway."""
    r = _post(client, host="host-b", verdict="wrong",
              actual_root_cause="x", actual_fix="y")
    assert r.status_code == 409
    assert "playbook" in r.json()["detail"]["error"].lower()


def test_deleting_a_playbook_is_refused(client):
    r = client.delete("/api/memory/pt-playbook?confirm=true")
    assert r.status_code == 409


# ── deprecation ─────────────────────────────────────────────────────────────
def test_deprecating_an_analysis_is_global(client):
    r = client.post("/api/memory/pt-analysis/deprecate?tenant_id=acme")
    assert r.status_code == 200 and r.json()["scope"] == "global"
    assert "pt-analysis" in client.store.deprecated


def test_deprecating_a_playbook_is_per_tenant(client):
    """One tenant retiring shipped advice must not hide it from everyone else."""
    r = client.post("/api/memory/pt-playbook/deprecate?tenant_id=acme")
    assert r.status_code == 200 and r.json()["scope"] == "tenant"
    assert "pt-playbook" not in client.store.deprecated          # shared point untouched
    assert feedback_store.deprecated_playbook_ids("acme") == {"pt-playbook"}
    assert feedback_store.deprecated_playbook_ids("other-corp") == set()


# ── delete guard ────────────────────────────────────────────────────────────
def test_delete_requires_explicit_confirmation(client):
    assert client.delete("/api/memory/pt-analysis").status_code == 400


# ── links survive dedup ─────────────────────────────────────────────────────
def test_link_is_replaced_not_duplicated(tmp_path, monkeypatch):
    monkeypatch.setattr(feedback_store, "_DB_PATH", tmp_path / "t2.db")
    feedback_store.init_feedback_tables()
    feedback_store.link_memory_point("1", "h", "t", "point-a")
    feedback_store.link_memory_point("1", "h", "t", "point-b")
    assert feedback_store.get_memory_point("1", "h")["point_id"] == "point-b"


def test_missing_link_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(feedback_store, "_DB_PATH", tmp_path / "t3.db")
    feedback_store.init_feedback_tables()
    assert feedback_store.get_memory_point("999", "nope") is None
