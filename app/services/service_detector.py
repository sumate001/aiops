"""Detect which database engine a host's logs come from.

A4 can only filter memory/playbook hits by service if it knows the engine, and
the ingest payload often doesn't say. This is deliberately regex-only — no LLM,
no model load — because it runs on every window.

Guessing wrong is worse than not guessing: a wrong `service` filter hides the
one relevant hit, while no filter merely widens the search. So detection has to
clear both a dominance bar (this engine, not another) and an evidence bar
(enough matching lines to mean it), otherwise it returns None.
"""
from __future__ import annotations

import json
import re

# Patterns are weighted: a token that essentially only ever appears in one
# engine's logs (`[MY-012345]`, `WiredTiger`) counts for much more than a word
# that engine merely uses often (`mysqld`, `oplog`).
_STRONG, _WEAK = 3, 1

_PATTERNS: dict[str, list[tuple[re.Pattern, int]]] = {
    "mysql": [
        (re.compile(r"\[MY-\d{6}\]"), _STRONG),          # MySQL 8 error-log code
        (re.compile(r"\bInnoDB:"), _STRONG),
        (re.compile(r"\[(?:Server|InnoDB)\]"), _STRONG),  # 8.0 subsystem tag
        (re.compile(r"\bERROR \d{4} \(\w{5}\)"), _STRONG),  # client: ERROR 1213 (40001)
        (re.compile(r"\bmysqld\b", re.I), _WEAK),
        (re.compile(r"\b(?:Aborted connection|Too many connections)\b", re.I), _WEAK),
        (re.compile(r"\b(?:innodb_\w+|slave_\w+|binlog)\b", re.I), _WEAK),
    ],
    "postgresql": [
        # `%m [%p] ` is the near-universal log_line_prefix, and the severity
        # always carries a colon. Requiring the [pid] is what keeps unrelated
        # "FATAL ERROR:" lines (Node's OOM message, for one) from scoring here.
        (re.compile(r"\[\d+\]\s*(?:\w+@\w+\s+)?(?:FATAL|PANIC|ERROR|LOG|WARNING|DETAIL|STATEMENT|HINT):"), _STRONG),
        (re.compile(r"\bautovacuum\b", re.I), _STRONG),
        (re.compile(r"\bdeadlock detected\b", re.I), _STRONG),  # MySQL says "Deadlock found"
        (re.compile(r"\bpg_[a-z_]{3,}\b"), _WEAK),
        (re.compile(r"\b(?:WAL|checkpoint|replication slot|wal_\w+)\b", re.I), _WEAK),
        (re.compile(r"\bSQLSTATE\b", re.I), _WEAK),
        # SQLSTATE classes that actually show up in incidents: connection (08),
        # integrity (23), serialization (40), resource (53), lock (55), operator (57)
        (re.compile(r"\b(?:08[0-9A-Z]{3}|23[0-9A-Z]{3}|40001|40P01|53[0-9A-Z]{3}|55P03|57014)\b"), _WEAK),
    ],
    "mongodb": [
        (re.compile(r"\bWiredTiger\b"), _STRONG),
        (re.compile(r'"codeName"\s*:'), _STRONG),
        (re.compile(r"\b(?:mongod|replica set|oplog)\b", re.I), _WEAK),
    ],
}

# Engines we ship playbooks for — an explicit `service` naming one of these is
# trusted over anything the patterns infer.
KNOWN_ENGINES = frozenset(_PATTERNS)

_ALIASES = {
    "mysql": "mysql", "mariadb": "mysql", "mysqld": "mysql", "percona": "mysql",
    "postgres": "postgresql", "postgresql": "postgresql", "psql": "postgresql",
    "pgsql": "postgresql", "postgres-db": "postgresql",
    "mongo": "mongodb", "mongodb": "mongodb", "mongod": "mongodb",
}

# Below this many matching lines, agreement is luck rather than evidence.
_MIN_EVIDENCE_LINES = 3


def _mongo_structural_score(line: str) -> int:
    """MongoDB 4.4+ writes one JSON object per line. Parsing beats a regex here:
    the give-away is the *shape* (its short `s`/`c`/`ctx` keys), not any word."""
    s = line.strip()
    if not (s.startswith("{") and s.endswith("}")):
        return 0
    try:
        obj = json.loads(s)
    except ValueError:
        return 0
    if not isinstance(obj, dict):
        return 0
    keys = set(obj)
    if {"s", "c", "ctx"} <= keys or {"t", "s", "msg"} <= keys or "codeName" in keys:
        return _STRONG
    return 0


def normalize_service(name: str | None) -> str | None:
    """Map a service label from ingest onto a known engine, or None."""
    if not name:
        return None
    return _ALIASES.get(name.strip().lower())


def detect_service(
    log_lines: list[str],
    sample: int = 50,
    min_confidence: float = 0.6,
) -> tuple[str | None, float]:
    """Infer the engine from log text.

    Returns `(engine, confidence)`, or `(None, confidence)` when the evidence
    is too thin or too split to act on — the confidence is still returned so
    callers can log how close it came.
    """
    lines = [ln for ln in log_lines[:sample] if ln and ln.strip()]
    if not lines:
        return None, 0.0

    scores: dict[str, int] = {e: 0 for e in _PATTERNS}
    hit_lines: dict[str, int] = {e: 0 for e in _PATTERNS}

    for line in lines:
        for engine, patterns in _PATTERNS.items():
            line_score = sum(w for pat, w in patterns if pat.search(line))
            if engine == "mongodb":
                line_score += _mongo_structural_score(line)
            if line_score:
                scores[engine] += line_score
                hit_lines[engine] += 1

    total = sum(scores.values())
    if total == 0:
        return None, 0.0

    engine = max(scores, key=lambda e: scores[e])
    # Dominance: how much of the evidence points at this engine rather than a
    # rival. Evidence: whether there was enough of it to be worth believing.
    dominance = scores[engine] / total
    evidence = min(1.0, hit_lines[engine] / _MIN_EVIDENCE_LINES)
    confidence = round(dominance * evidence, 3)

    if confidence < min_confidence:
        return None, confidence
    return engine, confidence


def resolve_service(
    explicit: str | None,
    log_lines: list[str],
    sample: int = 50,
    min_confidence: float = 0.6,
) -> tuple[str | None, float]:
    """Prefer what ingest already told us; fall back to sniffing the log text.

    An explicit label is only honoured when it names an engine we know — a
    service called "pos-api" says nothing about which database it talks to.
    """
    known = normalize_service(explicit)
    if known:
        return known, 1.0
    return detect_service(log_lines, sample=sample, min_confidence=min_confidence)
