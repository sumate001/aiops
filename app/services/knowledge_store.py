"""
Phase 2 — knowledge store สำหรับ known issues ต่อ (software, version)

หลักการ (ดู docs/prediction-topology-simulation-proposal.md ข้อ 3.2):
- key ตาม (name, version) ไม่ใช่ต่อ node — 20 เครื่อง MySQL 8.0 ใช้ profile เดียว
- แยก "การมีความรู้" (get_*) ออกจาก "การหาความรู้" (research queue) —
  analyze ใช้เท่าที่มี, profile ที่ยังไม่มีก็วิเคราะห์ได้ตามปกติ
- kind='compat': ความไม่เข้ากันระหว่าง software กับ OS ใน node เดียวกัน
- persist ลง SQLite เดิม — ความรู้สะสมรอด restart
"""

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_DB_PATH = "log_analyzer.db"

STALE_AFTER_DAYS = 30      # known bugs ต่อ version ไม่เปลี่ยนรายชั่วโมง
MAX_ATTEMPTS = 5
BACKOFF_MINUTES = [5, 15, 30, 60, 120]   # ต่อ attempt ที่ fail


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _now() -> datetime:
    return datetime.now(timezone.utc)


def init_knowledge_table() -> None:
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS software_knowledge (
                key             TEXT PRIMARY KEY,   -- normalized research key
                kind            TEXT NOT NULL,      -- 'version' | 'compat'
                name            TEXT NOT NULL,
                version         TEXT,
                os_name         TEXT,               -- compat: อีกฝั่งของคู่
                os_version      TEXT,
                status          TEXT NOT NULL DEFAULT 'pending',  -- pending|ok|failed
                priority        INTEGER NOT NULL DEFAULT 0,
                attempts        INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT,
                query           TEXT,
                answer          TEXT,
                sources         TEXT,
                researched_at   TEXT
            )
        """)


def _version_key(name: str, version: str | None) -> str:
    return f"{name} {version or ''}".strip().lower()


def _compat_key(name: str, version: str | None, os_name: str, os_version: str | None) -> str:
    return f"{_version_key(name, version)} @ {_version_key(os_name, os_version)}"


def enqueue_profile(
    name: str,
    version: str | None,
    kind: str = "version",
    os_name: str | None = None,
    os_version: str | None = None,
    priority: int = 0,
) -> bool:
    """เพิ่มเข้าคิวถ้ายังไม่รู้จัก; ถ้ามีอยู่แล้วแค่ยก priority ขึ้น (ไม่ลด)
    คืน True ถ้าเป็น profile ใหม่"""
    key = (_compat_key(name, version, os_name or "", os_version)
           if kind == "compat" else _version_key(name, version))
    with _conn() as c:
        existed = c.execute(
            "SELECT 1 FROM software_knowledge WHERE key=?", (key,)
        ).fetchone() is not None
        c.execute(
            """INSERT INTO software_knowledge
                   (key, kind, name, version, os_name, os_version, priority)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(key) DO UPDATE SET
                   priority = MAX(priority, excluded.priority)""",
            (key, kind, name, version, os_name, os_version, priority),
        )
        return not existed


def enqueue_from_topology(tenant_id: str) -> int:
    """สแกน node profiles ของ tenant → เข้าคิวทุก (software, version) ที่ยังไม่รู้จัก
    รวม compat pair เมื่อ node มีทั้ง software และ os ที่ต่างกัน"""
    from app.services import topology_store
    topo = topology_store.get_topology(tenant_id)
    if not topo:
        return 0
    added = 0
    for node in topo["nodes"]:
        os_info = node.get("os") or {}
        for sw in node.get("software") or []:
            if enqueue_profile(sw["name"], sw.get("version")):
                added += 1
            if os_info and os_info.get("name") and os_info["name"] != sw["name"]:
                if enqueue_profile(
                    sw["name"], sw.get("version"), kind="compat",
                    os_name=os_info["name"], os_version=os_info.get("version"),
                ):
                    added += 1
    logger.info("Knowledge queue: +%d new profiles from topology tenant=%s", added, tenant_id)
    return added


def enqueue_all_tenants() -> int:
    """เรียกตอน startup — topology ที่ upload ไว้ก่อนหน้าเข้าคิวด้วย"""
    with _conn() as c:
        tenants = [r["tenant_id"] for r in c.execute("SELECT tenant_id FROM topology_meta")]
    return sum(enqueue_from_topology(t) for t in tenants)


def bump_priority(name: str, version: str | None, priority: int = 10) -> None:
    """node ที่กำลังมี anomaly → profile ของมันขึ้นหัวคิว (และ requeue ถ้าเคย fail)"""
    with _conn() as c:
        c.execute(
            """UPDATE software_knowledge
               SET priority = MAX(priority, ?),
                   status = CASE WHEN status='failed' THEN 'pending' ELSE status END,
                   next_attempt_at = NULL
               WHERE key = ?""",
            (priority, _version_key(name, version)),
        )


def requeue_stale() -> int:
    """profile ที่ความรู้ครบอายุ → กลับเข้าคิว (re-research รายเดือน)"""
    cutoff = (_now() - timedelta(days=STALE_AFTER_DAYS)).isoformat()
    with _conn() as c:
        cur = c.execute(
            """UPDATE software_knowledge
               SET status='pending', attempts=0, next_attempt_at=NULL
               WHERE status='ok' AND researched_at < ?""",
            (cutoff,),
        )
        return cur.rowcount


def next_pending() -> dict | None:
    """งานถัดไปของ worker: priority สูงก่อน, แล้ว attempts น้อยก่อน"""
    now = _now().isoformat()
    with _conn() as c:
        row = c.execute(
            """SELECT * FROM software_knowledge
               WHERE status='pending'
                 AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
               ORDER BY priority DESC, attempts ASC, key ASC
               LIMIT 1""",
            (now,),
        ).fetchone()
    return dict(row) if row else None


def mark_ok(key: str, query: str, answer: str, sources: list[dict]) -> None:
    with _conn() as c:
        c.execute(
            """UPDATE software_knowledge
               SET status='ok', query=?, answer=?, sources=?, researched_at=?,
                   priority=0, next_attempt_at=NULL
               WHERE key=?""",
            (query, answer, json.dumps(sources), _now().isoformat(), key),
        )


def mark_failed(key: str) -> None:
    """fail → backoff ตามจำนวน attempt; เกิน MAX_ATTEMPTS → พักถาวรจนมี bump/stale"""
    with _conn() as c:
        row = c.execute(
            "SELECT attempts FROM software_knowledge WHERE key=?", (key,)
        ).fetchone()
        attempts = (row["attempts"] if row else 0) + 1
        if attempts >= MAX_ATTEMPTS:
            c.execute(
                "UPDATE software_knowledge SET status='failed', attempts=? WHERE key=?",
                (attempts, key),
            )
            return
        delay = BACKOFF_MINUTES[min(attempts - 1, len(BACKOFF_MINUTES) - 1)]
        c.execute(
            """UPDATE software_knowledge
               SET attempts=?, next_attempt_at=? WHERE key=?""",
            (attempts, (_now() + timedelta(minutes=delay)).isoformat(), key),
        )


def get_knowledge(name: str, version: str | None) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM software_knowledge WHERE key=? AND status='ok'",
            (_version_key(name, version),),
        ).fetchone()
    return dict(row) if row else None


def get_for_node(profile: dict) -> list[dict]:
    """ความรู้ทั้งหมดที่เกี่ยวกับ node หนึ่ง (version + compat) เท่าที่มีตอนนี้"""
    keys: list[str] = []
    os_info = profile.get("os") or {}
    for sw in profile.get("software") or []:
        keys.append(_version_key(sw["name"], sw.get("version")))
        if os_info.get("name") and os_info["name"] != sw["name"]:
            keys.append(_compat_key(sw["name"], sw.get("version"),
                                    os_info["name"], os_info.get("version")))
    if not keys:
        return []
    with _conn() as c:
        rows = c.execute(
            f"""SELECT * FROM software_knowledge
                WHERE status='ok' AND key IN ({','.join('?' * len(keys))})""",
            keys,
        ).fetchall()
    return [dict(r) for r in rows]


def queue_stats() -> dict:
    with _conn() as c:
        rows = c.execute(
            "SELECT status, COUNT(*) AS n FROM software_knowledge GROUP BY status"
        ).fetchall()
    return {r["status"]: r["n"] for r in rows}
