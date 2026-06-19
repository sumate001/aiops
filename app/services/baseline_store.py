"""
SQLite persistence layer — เก็บ stats ต่อ time-window ต่อ host
ใช้สำหรับ: baseline building, drift detection, trend analysis
"""

import json
import logging
import math
import os
import sqlite3
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("LA_DB_PATH", "log_analyzer.db")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    c = _conn()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS window_stats (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            host              TEXT    NOT NULL,
            tenant_id         TEXT    NOT NULL,
            window_from       TEXT    NOT NULL,
            window_to         TEXT    NOT NULL,
            entry_count       INTEGER NOT NULL,
            error_count       INTEGER NOT NULL,
            warn_count        INTEGER NOT NULL,
            error_rate        REAL    NOT NULL,
            health_score      REAL    NOT NULL,
            top_errors        TEXT,
            crash_count       INTEGER DEFAULT 0,
            auth_fail_count   INTEGER DEFAULT 0,
            payment_fail_count INTEGER DEFAULT 0,
            network_err_count INTEGER DEFAULT 0,
            db_err_count      INTEGER DEFAULT 0,
            hardware_err_count INTEGER DEFAULT 0,
            app_crash_count   INTEGER DEFAULT 0,
            recorded_at       TEXT    DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_ws_host_time
            ON window_stats(host, window_from DESC);
    """)
    # migrate: add new columns to existing DB if not present
    for col_def in [
        "ADD COLUMN payment_fail_count  INTEGER DEFAULT 0",
        "ADD COLUMN network_err_count   INTEGER DEFAULT 0",
        "ADD COLUMN db_err_count        INTEGER DEFAULT 0",
        "ADD COLUMN hardware_err_count  INTEGER DEFAULT 0",
        "ADD COLUMN app_crash_count     INTEGER DEFAULT 0",
    ]:
        try:
            c.execute(f"ALTER TABLE window_stats {col_def}")
        except Exception:
            pass  # column already exists
    c.commit()
    c.close()
    logger.debug("DB ready at %s", DB_PATH)


@dataclass
class WindowStat:
    host: str
    tenant_id: str
    window_from: str        # ISO-8601
    window_to: str
    entry_count: int
    error_count: int
    warn_count: int
    health_score: float
    top_error_msgs: list[str] = field(default_factory=list)
    crash_count: int = 0
    auth_fail_count: int = 0
    payment_fail_count: int = 0
    network_err_count: int = 0
    db_err_count: int = 0
    hardware_err_count: int = 0
    app_crash_count: int = 0


def save_window_stat(stat: WindowStat) -> None:
    error_rate = stat.error_count / stat.entry_count if stat.entry_count else 0.0
    c = _conn()
    try:
        c.execute("""
            INSERT INTO window_stats
                (host, tenant_id, window_from, window_to,
                 entry_count, error_count, warn_count, error_rate,
                 health_score, top_errors,
                 crash_count, auth_fail_count,
                 payment_fail_count, network_err_count, db_err_count,
                 hardware_err_count, app_crash_count)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            stat.host, stat.tenant_id,
            stat.window_from, stat.window_to,
            stat.entry_count, stat.error_count, stat.warn_count, error_rate,
            stat.health_score,
            json.dumps(stat.top_error_msgs[:5]),
            stat.crash_count, stat.auth_fail_count,
            stat.payment_fail_count, stat.network_err_count, stat.db_err_count,
            stat.hardware_err_count, stat.app_crash_count,
        ))
        c.commit()
    finally:
        c.close()


def get_recent_windows(host: str, limit: int = 20) -> list[dict]:
    c = _conn()
    try:
        rows = c.execute("""
            SELECT * FROM window_stats
            WHERE host = ?
            ORDER BY window_from DESC
            LIMIT ?
        """, (host, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def get_same_hour_baseline(
    host: str, hour_of_day: int, day_type: str, min_samples: int = 3
) -> dict | None:
    """ค่าเฉลี่ยสำหรับ hour เดียวกัน (weekday/weekend) — ใช้เป็น baseline เปรียบเทียบ"""
    c = _conn()
    try:
        row = c.execute("""
            SELECT
                AVG(error_rate)                                        AS avg_error_rate,
                AVG(health_score)                                      AS avg_health_score,
                COUNT(*)                                               AS sample_count,
                AVG(error_rate * error_rate) - AVG(error_rate) * AVG(error_rate)
                                                                       AS var_error_rate,
                AVG(crash_count)                                       AS avg_crash_count,
                AVG(auth_fail_count)                                   AS avg_auth_fail_count
            FROM window_stats
            WHERE host = ?
              AND CAST(strftime('%H', window_from) AS INTEGER) = ?
              AND CASE
                    WHEN strftime('%w', window_from) IN ('0','6') THEN 'weekend'
                    ELSE 'weekday'
                  END = ?
        """, (host, hour_of_day, day_type)).fetchone()

        if row and row["sample_count"] >= min_samples:
            return dict(row)
        return None
    finally:
        c.close()
