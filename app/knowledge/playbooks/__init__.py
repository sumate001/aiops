"""Playbook registry — cold-start knowledge seeded into the memory collection.

These ship with the system so A4 is useful on day one, before any real case has
been analysed or confirmed. They are *not* a fine-tune: seeding writes them into
the same collection A4 already searches, through the same retrieval path. No new
model, no GPU. To change one, edit the module and re-seed.

Not to be confused with `app/services/knowledge_store.py`, which is a different
thing entirely — per-(software, version) research findings kept in SQLite.

A playbook is general knowledge about an engine, never evidence that something
happened on this host. That distinction is what keeps it from inflating
confidence, so it is carried explicitly as `kind: "playbook"` in the payload.
"""
from __future__ import annotations

from app.knowledge.playbooks import mongodb, mysql, postgresql

KIND = "playbook"

# Bumped when entry content changes, so seeded points can be traced to a batch.
PLAYBOOK_VERSION = "2026.08.1"

_MODULES = {
    "mysql": mysql,
    "postgresql": postgresql,
    "mongodb": mongodb,
}

REQUIRED_FIELDS = (
    "id", "engine", "frame", "severity", "title", "symptom_text",
    "root_cause_chain", "fix_steps", "verify_steps", "docs_url",
)

VALID_FRAMES = frozenset({"Security", "Database", "Network", "Hardware", "Software"})
VALID_SEVERITIES = frozenset({"low", "medium", "high", "critical"})


def _validate(entry: dict, engine: str) -> None:
    """Fail loudly at import rather than seeding a malformed entry.

    A bad entry here is expensive: it lands in a persistent store and then shows
    up as advice, so it is worth catching before it ever gets embedded.
    """
    missing = [f for f in REQUIRED_FIELDS if not entry.get(f)]
    if missing:
        raise ValueError(f"playbook {entry.get('id', '<no id>')}: missing {missing}")
    if entry["engine"] != engine:
        raise ValueError(f"playbook {entry['id']}: engine {entry['engine']!r} != module {engine!r}")
    if not entry["id"].startswith(f"{engine}."):
        raise ValueError(f"playbook {entry['id']}: id must start with {engine!r}")
    if entry["frame"] not in VALID_FRAMES:
        raise ValueError(f"playbook {entry['id']}: unknown frame {entry['frame']!r}")
    if entry["severity"] not in VALID_SEVERITIES:
        raise ValueError(f"playbook {entry['id']}: unknown severity {entry['severity']!r}")
    for list_field in ("root_cause_chain", "fix_steps", "verify_steps"):
        if not isinstance(entry[list_field], list):
            raise TypeError(f"playbook {entry['id']}: {list_field} must be a list")


def all_entries(engines: list[str] | None = None) -> list[dict]:
    """Every playbook, optionally narrowed to some engines. Validated on the way out."""
    wanted = list(_MODULES) if engines is None else [e for e in engines if e in _MODULES]
    out: list[dict] = []
    seen_ids: set[str] = set()
    for engine in wanted:
        for entry in _MODULES[engine].PLAYBOOK_ENTRIES:
            _validate(entry, engine)
            if entry["id"] in seen_ids:
                raise ValueError(f"duplicate playbook id: {entry['id']}")
            seen_ids.add(entry["id"])
            out.append(entry)
    return out


def entries_for(engine: str) -> list[dict]:
    """Playbooks for one engine ("mysql"), or [] if we don't ship any."""
    return all_entries([engine]) if engine in _MODULES else []


def available_engines() -> list[str]:
    return sorted(_MODULES)
