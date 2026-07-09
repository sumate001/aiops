from datetime import datetime, timedelta, timezone

import pytest

from app.models.topology import TopologyIngestRequest
from app.services import knowledge_store, research_worker, topology_store


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    db = str(tmp_path / "kn.db")
    monkeypatch.setattr(knowledge_store, "_DB_PATH", db)
    monkeypatch.setattr(topology_store, "_DB_PATH", db)
    knowledge_store.init_knowledge_table()
    topology_store.init_topology_tables()


def _load_topology():
    req = TopologyIngestRequest.model_validate({
        "tenant_id": "t1",
        "nodes": [
            {"node_id": "db-1", "software": [{"name": "MySQL", "version": "8.0"}],
             "os": {"name": "RHEL", "version": "8"}},
            {"node_id": "db-2", "software": [{"name": "MySQL", "version": "8.0"}],
             "os": {"name": "RHEL", "version": "8"}},
            {"node_id": "app-1", "software": [{"name": "Ubuntu", "version": "22.04"}],
             "os": {"name": "Ubuntu", "version": "22.04"}},
        ],
    })
    topology_store.save_topology("t1", req.nodes, req.edges, None)


def test_enqueue_from_topology_dedupes():
    _load_topology()
    added = knowledge_store.enqueue_from_topology("t1")
    # MySQL 8.0 (version) + MySQL@RHEL (compat) + Ubuntu 22.04 (version;
    # os ชื่อเดียวกับ software → ไม่มี compat)
    assert added == 3
    assert knowledge_store.enqueue_from_topology("t1") == 0  # ซ้ำไม่เพิ่ม
    assert knowledge_store.queue_stats() == {"pending": 3}


def test_priority_and_backoff_ordering():
    knowledge_store.enqueue_profile("MySQL", "8.0")
    knowledge_store.enqueue_profile("Redis", "7")
    knowledge_store.bump_priority("Redis", "7")
    assert knowledge_store.next_pending()["key"] == "redis 7"

    # fail → backoff อนาคต → หลุดจากหัวคิว
    knowledge_store.mark_failed("redis 7")
    assert knowledge_store.next_pending()["key"] == "mysql 8.0"

    # fail จน MAX_ATTEMPTS → failed ถาวร
    for _ in range(knowledge_store.MAX_ATTEMPTS):
        knowledge_store.mark_failed("mysql 8.0")
    assert knowledge_store.queue_stats()["failed"] == 1
    # bump → กลับมา pending ทันที
    knowledge_store.bump_priority("MySQL", "8.0")
    assert knowledge_store.next_pending()["key"] == "mysql 8.0"


def test_mark_ok_and_get_for_node():
    _load_topology()
    knowledge_store.enqueue_from_topology("t1")
    knowledge_store.mark_ok("mysql 8.0", "q", "known connection leak bug", [{"url": "x"}])
    node = topology_store.get_node("t1", "db-1")
    got = knowledge_store.get_for_node(node)
    assert len(got) == 1 and "connection leak" in got[0]["answer"]
    assert knowledge_store.get_knowledge("MySQL", "8.0")["status"] == "ok"
    assert knowledge_store.get_knowledge("MySQL", "5.7") is None


def test_requeue_stale(monkeypatch):
    knowledge_store.enqueue_profile("MySQL", "8.0")
    knowledge_store.mark_ok("mysql 8.0", "q", "a", [])
    assert knowledge_store.requeue_stale() == 0
    old = (datetime.now(timezone.utc)
           - timedelta(days=knowledge_store.STALE_AFTER_DAYS + 1)).isoformat()
    with knowledge_store._conn() as c:
        c.execute("UPDATE software_knowledge SET researched_at=?", (old,))
    assert knowledge_store.requeue_stale() == 1
    assert knowledge_store.queue_stats() == {"pending": 1}


@pytest.mark.asyncio
async def test_worker_process_next_success_and_failure():
    knowledge_store.enqueue_profile("MySQL", "8.0")
    knowledge_store.enqueue_profile(
        "MySQL", "8.0", kind="compat", os_name="RHEL", os_version="8")

    seen: list[str] = []

    async def fake_search(query: str):
        seen.append(query)
        return {"answer": "found issues", "sources": [{"url": "u"}]}

    assert await research_worker.process_next(search=fake_search) is True
    assert await research_worker.process_next(search=fake_search) is True
    assert await research_worker.process_next(search=fake_search) is False  # คิวว่าง
    assert any("compatibility" in q for q in seen)
    assert knowledge_store.queue_stats() == {"ok": 2}

    knowledge_store.enqueue_profile("Redis", "7")

    async def failing_search(query: str):
        return None

    assert await research_worker.process_next(search=failing_search) is False
    row = knowledge_store._conn().execute(
        "SELECT attempts, next_attempt_at FROM software_knowledge WHERE key='redis 7'"
    ).fetchone()
    assert row["attempts"] == 1 and row["next_attempt_at"] is not None


@pytest.mark.asyncio
async def test_research_now_uses_existing_knowledge():
    knowledge_store.enqueue_profile("MySQL", "8.0")
    knowledge_store.mark_ok("mysql 8.0", "q", "cached answer", [])

    called = False

    async def fake_search(query: str):
        nonlocal called
        called = True
        return {"answer": "new", "sources": []}

    got = await research_worker.research_now("MySQL", "8.0")
    assert got["answer"] == "cached answer"
    assert called is False
