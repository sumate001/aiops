"""Playbook content checks.

These entries get embedded into a persistent store and then surface as advice,
so the cost of a bad one is higher than usual — validate before it ever reaches
Qdrant, not after.
"""
import pytest

from app.knowledge import playbooks
from app.services.service_detector import KNOWN_ENGINES


def test_every_engine_has_entries():
    for engine in playbooks.available_engines():
        assert len(playbooks.entries_for(engine)) >= 10, engine


def test_engines_match_what_the_detector_can_identify():
    """A playbook for an engine the detector can't name would never be filtered
    to, and a detected engine with no playbooks is a silent coverage hole."""
    assert set(playbooks.available_engines()) == set(KNOWN_ENGINES)


def test_ids_are_unique_and_namespaced():
    entries = playbooks.all_entries()
    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids))
    for e in entries:
        assert e["id"].startswith(e["engine"] + ".")


def test_all_entries_validate():
    # all_entries() validates on the way out; this pins that it actually runs
    assert len(playbooks.all_entries()) == sum(
        len(playbooks.entries_for(e)) for e in playbooks.available_engines()
    )


def test_engine_filter():
    assert {e["engine"] for e in playbooks.all_entries(["mysql"])} == {"mysql"}
    assert playbooks.all_entries(["nosuchdb"]) == []
    assert playbooks.entries_for("nosuchdb") == []


# ── content quality ─────────────────────────────────────────────────────────
def test_symptom_text_is_substantial():
    """symptom_text is the embedded field — a thin one just won't be retrieved."""
    for e in playbooks.all_entries():
        assert len(e["symptom_text"].split()) >= 15, e["id"]


def test_fix_steps_are_actionable():
    """The spec asks for runnable commands, not 'ตรวจสอบ configuration'."""
    hints = ("SELECT", "SHOW", "db.", "rs.", "sh.", "ALTER", "SET", "VACUUM", "EXPLAIN",
             "df ", "du ", "dmesg", "systemctl", "pg_", "KILL", "PURGE", "smartctl", "ulimit")
    for e in playbooks.all_entries():
        joined = " ".join(e["fix_steps"])
        assert any(h in joined for h in hints), f"{e['id']}: no concrete command in fix_steps"


def test_every_entry_has_verify_steps():
    """Playbooks are hypotheses, not findings — AA is told to make the operator
    confirm before acting, which only works if there is something to confirm."""
    for e in playbooks.all_entries():
        assert len(e["verify_steps"]) >= 1, e["id"]


def test_error_codes_are_strings():
    """They feed the sparse/BM25 side of the hybrid search, where "1205" and
    1205 are not the same token."""
    for e in playbooks.all_entries():
        for code in e.get("error_codes", []):
            assert isinstance(code, str), f"{e['id']}: {code!r} is not a str"


def test_docs_url_points_somewhere_real():
    for e in playbooks.all_entries():
        assert e["docs_url"].startswith("https://"), e["id"]


# ── validation actually rejects bad input ───────────────────────────────────
@pytest.mark.parametrize("mutate,expected", [
    (lambda e: e.pop("symptom_text"), ValueError),
    (lambda e: e.update(frame="Kitchen"), ValueError),
    (lambda e: e.update(severity="apocalyptic"), ValueError),
    (lambda e: e.update(engine="postgresql"), ValueError),
    (lambda e: e.update(fix_steps="not a list"), TypeError),
])
def test_validation_rejects_malformed_entries(mutate, expected):
    entry = dict(playbooks.entries_for("mysql")[0])
    mutate(entry)
    with pytest.raises(expected):
        playbooks._validate(entry, "mysql")
