"""A2 query construction + the judge's source gate.

Both guard the same failure: an over-long query returns zero web results, and
Perplexica answers anyway from its own model's memory. That sourceless prose
used to reach the AA judge labelled as web research.
"""
import pytest

from app.services.perplexica_client import _MAX_QUERY_WORDS, _clean_error, build_query

DEADLOCK = "ERROR 1213 (40001): Deadlock found when trying to get lock; try restarting transaction"
EXT4 = "kernel: EXT4-fs error (device sda1): ext4_find_entry:1455: inode #2"


def _q(frame=None, keywords=None, errors=None, metrics=None):
    return build_query(frame, keywords or [], errors or [], "pos-cluster-01",
                       anomaly_metrics=metrics)


# ── length budget ───────────────────────────────────────────────────────────
def test_query_never_exceeds_word_budget():
    """The engines intersect terms, so every extra concept shrinks the result
    set. Measured on this instance: 2 words → 10-40 hits, 8 → 10, 13 → 0."""
    for q in (
        _q("Database", ["slow query", "deadlock", "connection pool"], [DEADLOCK]),
        _q("Hardware", [], [EXT4]),
        _q("Software", [], [], ["cpu_usage", "memory_usage"]),
        _q("Network", ["a b c d e f g"], []),
    ):
        assert 0 < len(q.split()) <= _MAX_QUERY_WORDS, q


def test_does_not_concatenate_every_signal():
    """Regression: the old builder emitted frame + 3 keywords + an 8-word error
    phrase ('database slow query deadlock connection pool error deadlock found
    when trying to get lock troubleshooting') which returned zero results."""
    q = _q("Database", ["slow query", "deadlock", "connection pool"], [DEADLOCK])
    assert "deadlock" not in q or "slow query" not in q


def test_no_troubleshooting_suffix():
    """Measured: the suffix is an extra intersecting term that costs results
    ('deadlock found lock' → 10 hits, with the suffix → 5; 'kernel ext4 fs' →
    10, with it → 0) and never gained any."""
    assert "troubleshooting" not in _q("Database", ["deadlock"], [DEADLOCK])


# ── signal priority ─────────────────────────────────────────────────────────
def test_prefers_curated_keyword_over_error_prose():
    """Keywords beat the surrounding prose — but not a specific error code,
    which is checked separately below. Here the error line carries no code."""
    assert _q("Database", ["deadlock"], ["the database appears to be stuck"]) == "deadlock"


def test_falls_back_to_error_phrase_without_keywords():
    q = _q("Hardware", [], [EXT4])
    assert "ext4" in q


def test_falls_back_to_metrics_when_no_logs():
    assert _q("Software", [], [], ["cpu_usage", "memory_usage"]) == "cpu usage high"


# ── when to skip A2 entirely ────────────────────────────────────────────────
def test_frame_alone_is_not_searchable():
    """'hardware' does return results — 10 generic ones. Those are worse than
    no research: they carry real URLs, so they pass the judge's source gate
    while saying nothing about this incident."""
    assert _q("Hardware") == ""


def test_no_evidence_returns_empty():
    assert _q() == ""


# ── error de-noising ────────────────────────────────────────────────────────
def test_clean_error_drops_grammar_filler():
    cleaned = _clean_error(DEADLOCK)
    assert "deadlock" in cleaned
    for filler in ("when", "trying", "to", "get"):
        assert filler not in cleaned.split()


def test_clean_error_keeps_domain_words():
    cleaned = _clean_error("connection failed: timeout waiting for pool")
    assert "failed" in cleaned and "timeout" in cleaned


# ── error codes are the best possible query ─────────────────────────────────
@pytest.mark.parametrize("error,expected", [
    ("ORA-01555: snapshot too old on tablespace USERS", "ora-01555"),
    ("write to /var/lib/mysql/ibdata1 failed: errno 28", "errno 28"),
    ("upstream 10.0.0.5:8080 returned HTTP 503 after 3 retries", "http 503"),
    ("E11000 duplicate key error collection: orders", "e11000"),
])
def test_error_code_wins_over_keywords(error, expected):
    """A code names one specific failure; the prose around it describes a whole
    family. It's also short, which matters when the engines intersect terms."""
    assert _q("Database", ["slow query", "deadlock"], [error]) == expected


def test_clean_error_no_longer_swallows_codes():
    """Regression: the old local regex dropped every number of 3+ digits, taking
    ORA-01555 and HTTP 503 with it — exactly the tokens most likely to find the
    right page."""
    assert "ora-01555" in _clean_error("ORA-01555: snapshot too old")
    assert "503" in _clean_error("upstream returned HTTP 503")


def test_clean_error_still_drops_the_varying_parts():
    cleaned = _clean_error("2026-08-06T02:10:01Z txn 3f2504e0-4f89-11d3-9a0c-0305e82c3301 aborted")
    assert "2026" not in cleaned and "3f2504e0" not in cleaned
    assert "aborted" in cleaned
