"""SQLite side of the feedback loop.

Two small tables that Qdrant is the wrong home for:

**memory_links** maps `(result_id, host)` to the memory point it produced.
Looking the point up by its `result_id` payload instead would quietly break on
deduplicated cases: when the same symptom recurs, the existing point is reused
and keeps the *first* occurrence's result_id, so the newest analysis would have
nothing to attach feedback to.

**playbook_overrides** records that one tenant retired a shipped playbook.
Playbooks live under `__global__` and are shared, so marking `deprecated` on the
point itself would hide it from every other tenant — and the next re-seed would
overwrite the flag anyway. Per-tenant, outside the seeded data, is the only
arrangement that survives both.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_DB_PATH = Path("log_analyzer.db")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_feedback_tables() -> None:
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS memory_links (
                result_id   TEXT NOT NULL,
                host        TEXT NOT NULL,
                tenant_id   TEXT NOT NULL,
                point_id    TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                PRIMARY KEY (result_id, host)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS playbook_overrides (
                tenant_id   TEXT NOT NULL,
                point_id    TEXT NOT NULL,
                deprecated  INTEGER NOT NULL DEFAULT 1,
                note        TEXT,
                created_at  TEXT NOT NULL,
                PRIMARY KEY (tenant_id, point_id)
            )
        """)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── memory links ────────────────────────────────────────────────────────────
def link_memory_point(result_id: str, host: str, tenant_id: str, point_id: str) -> None:
    try:
        with _conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO memory_links "
                "(result_id, host, tenant_id, point_id, created_at) VALUES (?,?,?,?,?)",
                (str(result_id), host, tenant_id, point_id, _now()),
            )
    except Exception as exc:
        logger.warning("Failed to link memory point %s: %s", point_id, exc)


def get_memory_point(result_id: str, host: str) -> dict | None:
    try:
        with _conn() as c:
            row = c.execute(
                "SELECT * FROM memory_links WHERE result_id=? AND host=?",
                (str(result_id), host),
            ).fetchone()
            return dict(row) if row else None
    except Exception as exc:
        logger.warning("Failed to read memory link: %s", exc)
        return None


# ── playbook overrides ──────────────────────────────────────────────────────
def deprecate_playbook_for_tenant(tenant_id: str, point_id: str, note: str | None = None) -> None:
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO playbook_overrides "
            "(tenant_id, point_id, deprecated, note, created_at) VALUES (?,?,1,?,?)",
            (tenant_id, point_id, note, _now()),
        )


def deprecated_playbook_ids(tenant_id: str) -> set[str]:
    try:
        with _conn() as c:
            rows = c.execute(
                "SELECT point_id FROM playbook_overrides WHERE tenant_id=? AND deprecated=1",
                (tenant_id,),
            ).fetchall()
            return {r["point_id"] for r in rows}
    except Exception as exc:
        logger.warning("Failed to read playbook overrides: %s", exc)
        return set()
