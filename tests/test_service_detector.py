"""Engine detection for A4's service filter.

The bar is asymmetric on purpose: a wrong filter hides the one relevant hit,
while no filter just widens the search. So these tests care more about not
guessing than about guessing.
"""
from app.services.service_detector import (
    KNOWN_ENGINES,
    detect_service,
    normalize_service,
    resolve_service,
)

MYSQL = [
    "2026-08-06T02:10:01.123456Z 8 [ERROR] [MY-012345] [InnoDB] Operating system error number 28",
    "2026-08-06T02:10:02.123456Z 9 [ERROR] InnoDB: Lock wait timeout exceeded; try restarting transaction",
    "2026-08-06T02:10:03.123456Z 0 [System] [MY-010910] [Server] /usr/sbin/mysqld: Shutdown complete (mysqld 8.0.32)",
    "2026-08-06T02:10:04.123456Z 11 [Warning] Aborted connection 4711 to db: 'orders'",
]
POSTGRES = [
    "2026-08-06 02:10:01.123 UTC [12345] ERROR:  deadlock detected",
    "2026-08-06 02:10:01.124 UTC [12345] DETAIL:  Process 12345 waits for ShareLock on transaction 987",
    "2026-08-06 02:10:02.001 UTC [12346] FATAL:  sorry, too many clients already",
    "2026-08-06 02:10:03.500 UTC [12347] LOG:  automatic vacuum of table \"public.orders\": index scans: 1",
]
MONGO = [
    '{"t":{"$date":"2026-08-06T02:10:01.123+00:00"},"s":"E","c":"STORAGE","id":22435,"ctx":"conn12","msg":"WiredTiger error"}',
    '{"t":{"$date":"2026-08-06T02:10:02.123+00:00"},"s":"I","c":"NETWORK","id":22943,"ctx":"listener","msg":"Connection accepted"}',
    '{"t":{"$date":"2026-08-06T02:10:03.123+00:00"},"s":"W","c":"REPL","id":21400,"ctx":"conn9","msg":"Replication slower than oplog window"}',
]


# ── the happy path ──────────────────────────────────────────────────────────
def test_detects_each_engine():
    for lines, expected in ((MYSQL, "mysql"), (POSTGRES, "postgresql"), (MONGO, "mongodb")):
        engine, conf = detect_service(lines)
        assert engine == expected, f"{expected}: got {engine} ({conf})"
        assert conf >= 0.6


def test_all_detected_engines_have_playbook_coverage():
    for lines in (MYSQL, POSTGRES, MONGO):
        engine, _ = detect_service(lines)
        assert engine in KNOWN_ENGINES


# ── refusing to guess ───────────────────────────────────────────────────────
def test_unidentifiable_logs_return_none():
    engine, conf = detect_service([
        "2026-08-06 02:10:01 INFO  starting order-service v1.4.2",
        "2026-08-06 02:10:02 INFO  listening on :8080",
        "2026-08-06 02:10:03 WARN  cache miss ratio 0.42",
    ])
    assert engine is None


def test_empty_input_returns_none():
    assert detect_service([]) == (None, 0.0)
    assert detect_service(["", "   "]) == (None, 0.0)


def test_node_oom_is_not_mistaken_for_postgres():
    """'FATAL ERROR:' appears verbatim in Node's heap-OOM message. PostgreSQL's
    severity is only meaningful after its `[pid]` prefix — without that anchor
    this line scores as Postgres and every Node crash gets a DB playbook."""
    engine, _ = detect_service([
        "2026-08-06 02:10:01 FATAL ERROR: CALL_AND_RETRY_LAST Allocation failed - JavaScript heap out of memory",
    ] * 5)
    assert engine != "postgresql"


def test_single_weak_line_is_not_enough():
    """One passing mention shouldn't set the filter for the whole host."""
    engine, conf = detect_service(["2026-08-06 02:10:01 INFO connecting to mysqld on db-1"])
    assert engine is None
    assert conf < 0.6


def test_mixed_engines_stay_ambiguous():
    """A host tailing two engines gives a split vote — better to search wide."""
    engine, _ = detect_service(MYSQL[:2] + POSTGRES[:2])
    assert engine is None


# ── sampling ────────────────────────────────────────────────────────────────
def test_only_samples_the_first_n_lines():
    noise = ["2026-08-06 02:10:01 INFO nothing to see here"] * 60
    assert detect_service(noise + MYSQL, sample=50)[0] is None
    assert detect_service(MYSQL + noise, sample=50)[0] == "mysql"


# ── explicit service wins ───────────────────────────────────────────────────
def test_explicit_known_service_beats_detection():
    engine, conf = resolve_service("postgres", MYSQL)
    assert (engine, conf) == ("postgresql", 1.0)


def test_explicit_unknown_service_falls_back_to_detection():
    """"pos-api" says nothing about which database it talks to."""
    assert resolve_service("pos-api", MYSQL)[0] == "mysql"


def test_no_explicit_service_falls_back_to_detection():
    assert resolve_service(None, MONGO)[0] == "mongodb"


def test_alias_normalisation():
    assert normalize_service("MariaDB") == "mysql"
    assert normalize_service("Postgres") == "postgresql"
    assert normalize_service("mongod") == "mongodb"
    assert normalize_service("pos-api") is None
    assert normalize_service(None) is None
