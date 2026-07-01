"""
POS AIOps Knowledge Base
รวบรวมจาก IEEE Research, Industry Best Practice, และ Real-world POS Failure Cases

ใช้เป็น:
  1. Failure Fingerprints → predictor.py  (match pattern → ชื่อ scenario + lead time)
  2. Signal Patterns      → extractor      (keyword → signal type จาก log messages)
  3. Noise Patterns       → predictor.py  (ลด false alert)
  4. KPI Thresholds       → predictor.py  (ประเมิน severity ของแต่ละ signal)
  5. Ollama Context       → ollama.py     (เพิ่ม domain knowledge ให้ LLM)
"""

from __future__ import annotations

# ─── 1. Failure Fingerprints ─────────────────────────────────────────────────
# แต่ละ fingerprint คือ pattern ที่เห็นใน log 30-60 นาทีก่อน incident จริง
# (ตาม KB: Scenario 1-3 + POS-specific scenarios)

POS_FAILURE_FINGERPRINTS: list[dict] = [
    {
        "name": "Database Overload",
        "lead_time_minutes": 45,
        "description": "Query time สูงขึ้น + connection pool เพิ่ม → Too many connections",
        "required_signals": ["db_err"],           # ต้องมี
        "supporting_signals": ["crash", "auth_fail"],  # เสริม confidence
        "multi_signal_threshold": 2,
        "related_frame": "Database",
    },
    {
        "name": "Auth Failure Cascade",
        "lead_time_minutes": 45,
        "description": "SQL auth fail → service connect DB ไม่ได้ → crash loop",
        "required_signals": ["auth_fail", "crash"],
        "supporting_signals": ["db_err"],
        "multi_signal_threshold": 2,
        "related_frame": "Security",
    },
    {
        "name": "Network Failure",
        "lead_time_minutes": 30,
        "description": "Latency สูงขึ้น + connection drop บ่อยขึ้น → offline mode",
        "required_signals": ["network_err"],
        "supporting_signals": ["auth_fail"],
        "multi_signal_threshold": 1,
        "related_frame": "Network",
    },
    {
        "name": "Memory Leak / Resource Exhaustion",
        "lead_time_minutes": 120,
        "description": "Memory ค่อยๆ เพิ่ม + GC pause บ่อย → application crash ใน 2-4 ชม.",
        "required_signals": ["app_crash"],
        "supporting_signals": ["crash"],
        "multi_signal_threshold": 1,
        "related_frame": "Software",
    },
    {
        "name": "Payment Processing Failure",
        "lead_time_minutes": 20,
        "description": "Payment gateway timeout + retry เพิ่ม → transaction ล้มเหลว",
        "required_signals": ["payment_fail"],
        "supporting_signals": ["network_err", "db_err"],
        "multi_signal_threshold": 1,
        "related_frame": "Network",
    },
    {
        "name": "Hardware / Peripheral Failure",
        "lead_time_minutes": 15,
        "description": "Printer/scanner/terminal เริ่ม retry → อุปกรณ์จะหลุดทั้งหมด",
        "required_signals": ["hardware_err"],
        "supporting_signals": ["app_crash"],
        "multi_signal_threshold": 1,
        "related_frame": "Hardware",
    },
    {
        "name": "Service Crash Loop",
        "lead_time_minutes": 30,
        "description": "Service terminated unexpectedly ซ้ำๆ + error rate สูง",
        "required_signals": ["crash"],
        "supporting_signals": ["auth_fail", "db_err", "app_crash"],
        "multi_signal_threshold": 1,
        "related_frame": "Software",
    },
]

# ─── 2. Signal Extraction Patterns ───────────────────────────────────────────
# keyword → signal type  (จับจาก log message)
# ใช้ lowercase substring match

POS_SIGNAL_PATTERNS: dict[str, list[str]] = {
    "payment_fail": [
        "payment gateway", "authorization failed", "terminal refused",
        "transaction failed", "transaction timeout", "retry count exceeded",
        "duplicate transaction", "payment declined",
    ],
    "network_err": [
        "connection timeout", "lost connection", "network latency",
        "packet loss", "offline mode", "switching to offline",
        "reconnect", "connection refused", "host unreachable",
    ],
    "db_err": [
        "query timeout", "too many connections", "connection pool",
        "slow query", "db error", "database error", "sql error",
        "deadlock", "lock timeout", "max connections",
    ],
    "hardware_err": [
        "printer offline", "receipt not printed", "barcode scanner",
        "cash drawer", "card reader", "payment terminal not found",
        "peripheral", "device not found", "hardware fault",
    ],
    "app_crash": [
        "unresponsive", "watchdog triggered", "stack overflow",
        "out of memory", "gc pause", "garbage collection",
        "memory usage", "heap", "application crash",
        "maxmemory", "evicting keys", "eviction policy", "allkeys-lru",
        "key eviction", "redis oom", "cache full",
    ],
    "crash": [
        "terminated unexpectedly", "crashed", "restart the service",
        "segfault", "core dump", "aborted", "fatal error",
        "service failed", "process killed",
    ],
    "auth_fail": [
        "login failed", "authentication failed", "access denied",
        "password did not match", "unauthorized", "forbidden",
        "invalid credentials", "permission denied",
    ],
}

# ─── 3. Noise Patterns ────────────────────────────────────────────────────────
# สิ่งที่ดูเหมือนผิดปกติแต่จริงๆ เป็น routine ของ POS
# ถ้า match → ลด risk score + ระบุสาเหตุให้ชัด

POS_NOISE_PATTERNS: list[dict] = [
    {
        "name": "End-of-day Batch",
        "hours": list(range(22, 24)) + list(range(0, 5)),  # 22:00-04:59
        "keywords": ["batch", "end.of.day", "report", "reconcil", "settlement"],
        "reason": "End-of-day batch job — CPU/DB spike เป็นเรื่องปกติ",
        "risk_reduction": 20,
    },
    {
        "name": "Peak Hour Traffic",
        "hours": [11, 12, 13],  # 11:00-13:59
        "keywords": ["transaction", "high volume", "queue"],
        "reason": "Peak hour (11:00-13:00) — traffic สูงเป็นเรื่องปกติ",
        "risk_reduction": 15,
    },
    {
        "name": "Month-end DB Load",
        "day_of_month": list(range(28, 32)) + [1],
        "keywords": ["slow query", "db query", "query time"],
        "reason": "สิ้นเดือน — Batch job ปิดบัญชี ทำให้ DB ช้า",
        "risk_reduction": 10,
    },
    {
        "name": "Post-update Warmup",
        "keywords": ["warm up", "warmup", "initializing", "starting up", "software update"],
        "reason": "ระบบ warm up หลัง software update — memory เพิ่มชั่วคราว",
        "risk_reduction": 15,
    },
]

# ─── 4. KPI Danger Thresholds ─────────────────────────────────────────────────
# ใช้ประเมิน severity ของแต่ละ signal จาก KB

POS_KPI_THRESHOLDS: dict[str, dict] = {
    "transaction_success_rate": {"warning": 99.0, "critical": 97.0, "unit": "%", "direction": "below"},
    "transaction_response_time": {"warning": 2000, "critical": 5000, "unit": "ms", "direction": "above"},
    "failed_transaction_per_min": {"warning": 2, "critical": 10, "unit": "count", "direction": "above"},
    "retry_rate": {"warning": 3.0, "critical": 10.0, "unit": "%", "direction": "above"},
    "cpu_usage": {"warning": 70.0, "critical": 85.0, "unit": "%", "direction": "above"},
    "memory_usage": {"warning": 75.0, "critical": 85.0, "unit": "%", "direction": "above"},
    "network_latency": {"warning": 150, "critical": 300, "unit": "ms", "direction": "above"},
    "db_query_time": {"warning": 200, "critical": 1000, "unit": "ms", "direction": "above"},
    "connection_pool_usage": {"warning": 60.0, "critical": 80.0, "unit": "%", "direction": "above"},
    "slow_query_per_min": {"warning": 5, "critical": 20, "unit": "count", "direction": "above"},
}

# ─── 5. Ollama System Context ─────────────────────────────────────────────────
# prepend ใน prompt เพื่อให้ LLM เข้าใจ domain ของ POS

POS_OLLAMA_CONTEXT = """
You are an AIOps analyst specializing in POS (Point of Sale) systems used in retail environments (e.g., Big C, hypermarkets).

CRITICAL BUSINESS CONTEXT:
POS failure = customers cannot pay = immediate revenue loss. Every minute of downtime matters.

KNOWN POS FAILURE SCENARIOS (from real-world research):
1. Service Crash Loop      : Service terminated unexpectedly repeatedly → prevents all transactions
2. Auth Failure Cascade    : SQL auth fail → service cannot connect to DB → crash loop → POS offline
3. Database Overload       : Query slow (T-45min) → pool exhaustion (T-10min) → "Too many connections" (T-0)
4. Network Failure         : Latency drift (T-30min) → drops (T-10min) → offline mode (T-0)
5. Memory Leak             : Memory usage drift upward over 2-4 hours → GC pause → application crash
6. Payment Gateway Failure : Timeout/retry increase → authorization fail → transaction failure cascade
7. Hardware/Peripheral     : Retry failures increase → device offline → customer cannot transact

POS-SPECIFIC KPI DANGER THRESHOLDS:
- Transaction success rate < 97%      → CRITICAL (customers cannot pay)
- DB query time > 1000ms              → WARNING; > 2000ms → CRITICAL
- Connection pool > 80%               → WARNING; > 90% → CRITICAL
- Memory usage > 85% (rising trend)   → Memory leak suspected
- Auth failure rate > 5%              → Auth Failure Cascade risk

COMMON NOISE (do NOT over-alert):
- CPU/DB spike at 22:00-04:00         → End-of-day batch reports (normal)
- High transaction volume 11:00-13:00 → Peak hour (normal)
- Memory increase right after update  → Warm-up period (normal)
- Slow DB queries at month-end        → Accounting batch jobs (normal)

PMPOS-AGENT CONTEXT (from observed logs):
PMPOS-AGENT is a POS controller process. If it crashes repeatedly while SQL 'sa' login fails,
the most likely cause is the service using hardcoded/expired SQL credentials — not the service binary itself.
Resolution: reset SQL 'sa' password and update service config, then restart PMPOS-AGENT.
"""


def extract_signals_from_messages(messages: list[str]) -> dict[str, int]:
    """
    นับ signal count แต่ละประเภทจาก log messages
    Returns: {"payment_fail": 0, "network_err": 1, "db_err": 0, ...}
    """
    counts: dict[str, int] = {k: 0 for k in POS_SIGNAL_PATTERNS}
    for msg in messages:
        msg_lower = msg.lower()
        for signal_type, keywords in POS_SIGNAL_PATTERNS.items():
            if any(kw in msg_lower for kw in keywords):
                counts[signal_type] += 1
    return counts


def match_fingerprint(signal_counts: dict[str, int]) -> dict | None:
    """
    Match signal_counts กับ POS_FAILURE_FINGERPRINTS
    Returns: fingerprint dict ที่ match ดีที่สุด หรือ None
    """
    best: dict | None = None
    best_score = 0

    for fp in POS_FAILURE_FINGERPRINTS:
        # required signals ต้องมีทั้งหมด
        if not all(signal_counts.get(s, 0) > 0 for s in fp["required_signals"]):
            continue

        # score = required + supporting
        score = sum(1 for s in fp["required_signals"]   if signal_counts.get(s, 0) > 0)
        score += sum(1 for s in fp["supporting_signals"] if signal_counts.get(s, 0) > 0) * 0.5

        if score > best_score:
            best_score = score
            best = fp

    return best


def check_noise(top_error_msgs: list[str], hour: int, day_of_month: int) -> dict | None:
    """
    ตรวจว่า log pattern ปัจจุบันเป็น noise ตาม POS noise patterns หรือไม่
    Returns: noise pattern dict ถ้า match, หรือ None
    """
    all_text = " ".join(top_error_msgs).lower()
    for noise in POS_NOISE_PATTERNS:
        # check hour window
        if "hours" in noise and hour not in noise["hours"]:
            continue
        # check day of month
        if "day_of_month" in noise and day_of_month not in noise["day_of_month"]:
            continue
        # check keywords (OR)
        if any(kw in all_text for kw in noise["keywords"]):
            return noise
    return None
