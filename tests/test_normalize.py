"""Log normalisation for embedding.

Two opposing failures to guard against:
  - too little  → every occurrence of one incident embeds differently, memory
                  never matches anything
  - too much    → the diagnostic codes get blanked, and those are the most
                  identifiable tokens in the line
"""
import pytest

from app.services.normalize import (
    build_symptom_text,
    normalize_message,
    normalize_messages,
)


# ── varying parts get replaced ──────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected_token", [
    ("connection from 10.0.0.5 refused", "<IP>"),
    ("dial tcp 192.168.1.44:5432 connect: connection refused", "<IP>:<PORT>"),
    ("request 3f2504e0-4f89-11d3-9a0c-0305e82c3301 failed", "<UUID>"),
    ("2026-08-06T02:10:01.123456Z worker died", "<TS>"),
    ("started at 2026-08-06 shutting down", "<DATE>"),
    ("lock held by trx 0x7f8a9c2b1000", "<HEX>"),
    ("transaction 4211 waiting for lock", "<NUM>"),
    ("buffer pool 12.5GB exhausted", "<SIZE>"),
])
def test_varying_parts_are_replaced(raw, expected_token):
    assert expected_token in normalize_message(raw)


def test_same_incident_different_numbers_collapses():
    """The whole point: two occurrences must produce identical text."""
    a = normalize_message("Deadlock on order 88123 from 10.0.0.5:5432 at 2026-08-06T02:10:01Z")
    b = normalize_message("Deadlock on order 91007 from 10.0.0.9:5433 at 2026-08-07T19:44:52Z")
    assert a == b


def test_paths_keep_shape_but_lose_numbers():
    out = normalize_message("cannot write /var/lib/mysql/ibdata1 offset 104857600")
    assert "/var/lib/mysql/" in out
    assert "104857600" not in out


# ── diagnostic codes must survive ───────────────────────────────────────────
@pytest.mark.parametrize("raw,code", [
    ("ORA-01555: snapshot too old", "ORA-01555"),
    ("write failed: errno 28", "errno 28"),
    ("upstream returned HTTP 503", "HTTP 503"),
    ("ERROR 1213 (40001): Deadlock found", "ERROR 1213 (40001)"),
    ("[MY-012345] [InnoDB] operating system error", "MY-012345"),
    ("E11000 duplicate key error collection", "E11000"),
    ("SQLSTATE[40001] serialization failure", "SQLSTATE[40001]"),
    ("connect failed: ECONNREFUSED", "ECONNREFUSED"),
    ("container exited with exit code 137", "exit code 137"),
    ("killed by signal 9", "signal 9"),
])
def test_error_codes_are_not_swallowed(raw, code):
    """These are the most searchable tokens in the line — blanking them to
    <NUM> destroys exactly what makes the case identifiable."""
    assert code in normalize_message(raw)


def test_error_code_survives_alongside_noise():
    out = normalize_message(
        "2026-08-06T02:10:01Z [10.0.0.5:3306] ORA-01555 on txn 88231: snapshot too old"
    )
    assert "ORA-01555" in out
    assert "<TS>" in out and "<IP>:<PORT>" in out
    assert "88231" not in out


# ── edges ───────────────────────────────────────────────────────────────────
def test_empty_input():
    assert normalize_message("") == ""
    assert normalize_message(None or "") == ""


def test_whitespace_is_collapsed():
    assert normalize_message("a    b\t\tc") == "a b c"


# ── batch + dedupe ──────────────────────────────────────────────────────────
def test_dedupes_lines_that_collapse_together():
    msgs = [f"Deadlock on order {i} from 10.0.0.{i}" for i in range(20)]
    assert len(normalize_messages(msgs)) == 1


def test_limit_is_respected():
    msgs = ["deadlock found", "disk full errno 28", "connection refused", "oom killed"]
    assert len(normalize_messages(msgs, limit=2)) == 2


# ── symptom_text ────────────────────────────────────────────────────────────
def test_symptom_text_carries_every_signal():
    text = build_symptom_text(
        host="pos-cluster-01",
        service="mysql",
        status="critical",
        health_score=0.0,
        top_keywords=["deadlock", "connection pool", "deadlock"],
        frames=[{"frame": "Database", "relevance": 1.0}, {"frame": "Software", "relevance": 0.1}],
        anomaly_score=1.0,
        error_msgs=["ERROR 1213 (40001): Deadlock found on order 8812"],
    )
    assert "[mysql@pos-cluster-01]" in text
    assert "status=critical" in text
    assert "Database 1.00" in text
    assert "Software" not in text        # relevance <= 0.5 is dropped
    assert text.count("deadlock") == 1    # keywords deduped
    assert "ERROR 1213 (40001)" in text   # code survived into the sample
    assert "8812" not in text             # but the order id did not


def test_symptom_text_with_nothing_but_the_basics():
    text = build_symptom_text(host="h1", service=None, status="ok", health_score=100.0)
    assert "[unknown@h1]" in text
    assert "sample_errors" not in text


# ── error-code extraction (feeds A2's search query) ─────────────────────────
@pytest.mark.parametrize("msg,expected", [
    ("ORA-01555: snapshot too old", "ORA-01555"),
    ("write failed: errno 28", "errno 28"),
    ("upstream returned HTTP 503", "HTTP 503"),
    ("E11000 duplicate key error", "E11000"),
    ("[MY-012345] [InnoDB] operating system error", "MY-012345"),
])
def test_extract_error_codes_finds_the_identifying_token(msg, expected):
    from app.services.normalize import extract_error_codes
    assert expected in extract_error_codes(msg)


def test_extract_error_codes_returns_nothing_for_plain_prose():
    from app.services.normalize import extract_error_codes
    assert extract_error_codes("the database seems unhappy today") == []


def test_extract_error_codes_dedupes_and_keeps_order():
    from app.services.normalize import extract_error_codes
    codes = extract_error_codes("ORA-01555 then HTTP 503 then ORA-01555 again")
    assert codes == ["ORA-01555", "HTTP 503"]
