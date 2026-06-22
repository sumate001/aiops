"""
SQLite store สำหรับ analysis results — เก็บย้อนหลัง 500 รายการ
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_DB_PATH = Path("log_analyzer.db")
_MAX_RESULTS = 500


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_result_table() -> None:
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS analysis_results (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id   TEXT NOT NULL,
                analyzed_at TEXT NOT NULL,
                window_from TEXT,
                window_to   TEXT,
                health_score REAL,
                status      TEXT,
                host_count  INTEGER,
                critical_count INTEGER,
                summary     TEXT,
                payload     TEXT NOT NULL
            )
        """)


def save_result(result: dict) -> None:
    hosts = result.get("hosts", [])
    critical = sum(1 for h in hosts if h.get("status") == "critical")
    try:
        with _conn() as c:
            c.execute("""
                INSERT INTO analysis_results
                    (tenant_id, analyzed_at, window_from, window_to,
                     health_score, status, host_count, critical_count, summary, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                result.get("tenant_id", ""),
                result.get("analyzed_at", datetime.now(timezone.utc).isoformat()),
                result.get("window", {}).get("from"),
                result.get("window", {}).get("to"),
                result.get("health_score", 0),
                result.get("status", ""),
                len(hosts),
                critical,
                result.get("summary", ""),
                json.dumps(result),
            ))
            # prune เก็บแค่ MAX_RESULTS
            c.execute("""
                DELETE FROM analysis_results
                WHERE id NOT IN (
                    SELECT id FROM analysis_results ORDER BY id DESC LIMIT ?
                )
            """, (_MAX_RESULTS,))
    except Exception as exc:
        logger.warning("Failed to save analysis result: %s", exc)


def get_results(limit: int = 50, tenant_id: str | None = None) -> list[dict]:
    try:
        with _conn() as c:
            if tenant_id:
                rows = c.execute("""
                    SELECT id, tenant_id, analyzed_at, window_from, window_to,
                           health_score, status, host_count, critical_count, summary
                    FROM analysis_results WHERE tenant_id = ?
                    ORDER BY id DESC LIMIT ?
                """, (tenant_id, limit)).fetchall()
            else:
                rows = c.execute("""
                    SELECT id, tenant_id, analyzed_at, window_from, window_to,
                           health_score, status, host_count, critical_count, summary
                    FROM analysis_results
                    ORDER BY id DESC LIMIT ?
                """, (limit,)).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("Failed to get results: %s", exc)
        return []


def get_result_by_id(result_id: int) -> dict | None:
    try:
        with _conn() as c:
            row = c.execute(
                "SELECT payload FROM analysis_results WHERE id = ?", (result_id,)
            ).fetchone()
            if row:
                return json.loads(row["payload"])
    except Exception as exc:
        logger.warning("Failed to get result %s: %s", result_id, exc)
    return None
