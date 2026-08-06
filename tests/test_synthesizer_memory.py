"""How recalled cases reach the judge, and what they're allowed to do.

The confidence rules here are the whole point of Phase 2/3: memory must make the
system *better justified*, not merely more sure of itself.
"""
import json

import pytest

from app.models.response import MemoryHit
from app.services import synthesizer
from app.services.synthesizer import (
    MEM_SIM_MODERATE,
    MEM_SIM_STRONG,
    _build_judge_prompt,
    _rule_synthesis,
    synthesize,
)

FRAMES = [{"frame": "Database", "lens": "dba", "relevance": 1.0,
           "top_keywords": ["deadlock"], "signal_hits": 3}]
ANOMALIES = [{"metric": "isolation_forest", "score": 1.0, "severity": "high"}]


def _hit(kind="analysis", verified=False, similarity=0.95, point_id="p1", **kw):
    return MemoryHit(
        point_id=point_id, kind=kind, similarity=similarity,
        final_score=similarity, symptom_text="mysql deadlock on orders",
        root_cause_chain=["lock held too long"], fix_steps=["add index"],
        verified=verified, occurrence_count=kw.pop("occurrence_count", 1),
        days_ago=kw.pop("days_ago", 3), **kw,
    )


def _rule(memory_hits=None):
    return _rule_synthesis("h1", 10.0, ANOMALIES, FRAMES, memory_hits=memory_hits)


# ── confidence: only verified cases may move it ─────────────────────────────
def test_verified_strong_match_raises_confidence():
    base = _rule().confidence
    boosted = _rule([_hit(verified=True, similarity=MEM_SIM_STRONG)]).confidence
    assert boosted > base
    assert boosted <= 0.95


def test_verified_moderate_match_raises_less():
    base = _rule().confidence
    strong = _rule([_hit(verified=True, similarity=MEM_SIM_STRONG)]).confidence
    moderate = _rule([_hit(verified=True, similarity=MEM_SIM_MODERATE)]).confidence
    assert base < moderate < strong


def test_unverified_hit_never_moves_confidence():
    """Memory is this system's own past output. Letting an unconfirmed hit raise
    confidence closes a loop — "I guessed this before, so I'm surer now" — with
    no new evidence in it anywhere."""
    base = _rule().confidence
    assert _rule([_hit(verified=False, similarity=0.99)]).confidence == base


def test_playbook_hit_never_moves_confidence():
    """Shipped documentation isn't evidence that anything happened here."""
    base = _rule().confidence
    assert _rule([_hit(kind="playbook", similarity=0.99, title="known issue")]).confidence == base


def test_verified_but_weak_match_does_not_boost():
    base = _rule().confidence
    assert _rule([_hit(verified=True, similarity=0.75)]).confidence == base


def test_thresholds_sit_inside_the_models_real_range():
    """multilingual-e5-small never scores below ~0.72, so the plan's 0.65 tier
    would fire on everything. Both cuts must sit above the noise floor."""
    assert 0.72 < MEM_SIM_MODERATE < MEM_SIM_STRONG < 0.96


# ── the rule path must not lose memory ──────────────────────────────────────
def test_rule_fallback_still_surfaces_memory():
    """When the judge LLM fails we fall back to rules. If memory vanished there,
    it would disappear exactly when the system is least reliable — silently."""
    res = _rule([_hit(verified=True, similarity=0.95, actual_fix="rebuilt the index")])
    joined = " ".join(res.root_cause_chain)
    assert "Memory" in joined
    assert "rebuilt the index" in joined
    assert res.memory_influenced is True
    assert res.memory_refs == ["p1"]


def test_playbook_appears_but_is_labelled_as_general_knowledge():
    res = _rule([_hit(kind="playbook", point_id="kb1", title="InnoDB lock wait")])
    joined = " ".join(res.root_cause_chain)
    assert "Playbook" in joined
    assert res.playbook_refs == ["kb1"]
    assert res.memory_refs == []


def test_no_memory_leaves_everything_untouched():
    res = _rule([])
    assert res.memory_refs == [] and res.playbook_refs == []
    assert res.memory_influenced is False and res.playbook_influenced is False


# ── prompt rendering ────────────────────────────────────────────────────────
def _prompt(hits):
    return _build_judge_prompt("h1", 10.0, ANOMALIES, FRAMES, _rule(), memory_hits=hits)


def test_prompt_separates_memory_from_playbooks():
    """Merged into one block the judge can't tell evidence from documentation,
    and the rules about which may be relied on become unenforceable."""
    text = _prompt([_hit(point_id="mem1"), _hit(kind="playbook", point_id="kb1", title="T")])
    assert "INTERNAL MEMORY" in text and "PLAYBOOK" in text
    assert text.index("INTERNAL MEMORY") < text.index("## ความรู้อ้างอิง")
    assert "mem1" in text


def test_prompt_marks_verified_status():
    verified = _prompt([_hit(verified=True)])
    unverified = _prompt([_hit(verified=False)])
    assert "✓ ยืนยันแล้วโดยคน" in verified
    assert "ยังไม่ยืนยัน" in unverified


def test_prompt_is_explicit_when_nothing_was_recalled():
    text = _prompt([])
    assert "ไม่มีประวัติที่ตรงกัน" in text
    assert "ไม่มี playbook ที่ตรงกัน" in text


def test_prompt_forbids_inventing_ids():
    assert "ห้ามแต่ง point_id" in _prompt([_hit()])


# ── the judge must not invent citations ─────────────────────────────────────
@pytest.mark.asyncio
async def test_fabricated_point_ids_are_dropped():
    """A small model will happily produce a plausible-looking id. A fabricated
    citation is worse than none — it makes an unsupported claim look sourced."""
    async def fake_llm(**kwargs):
        return json.dumps({
            "root_cause_chain": ["something"], "confidence": 0.7,
            "fix_steps": ["do a thing"], "reasoning": "because",
            "memory_refs": ["real-1", "totally-made-up"],
            "playbook_refs": ["also-invented"],
        })

    res = await synthesize(
        host="h1", health_score=10.0, anomalies=ANOMALIES, mirofish_frames=FRAMES,
        rule_result=_rule(), memory_hits=[_hit(point_id="real-1")],
        use_llm=True, ollama_generate=fake_llm, model="m",
    )
    assert res.memory_refs == ["real-1"]
    assert res.playbook_refs == []


@pytest.mark.asyncio
async def test_llm_confidence_still_bounded_by_evidence_rules():
    """The judge may state its own confidence, but a verified match is what
    licenses a high one — and the ceiling holds regardless."""
    async def overconfident(**kwargs):
        return json.dumps({
            "root_cause_chain": ["x"], "confidence": 0.99, "fix_steps": ["y"],
            "reasoning": "z", "memory_refs": ["real-1"],
        })

    res = await synthesize(
        host="h1", health_score=10.0, anomalies=ANOMALIES, mirofish_frames=FRAMES,
        rule_result=_rule(),
        memory_hits=[_hit(point_id="real-1", verified=True, similarity=0.95)],
        use_llm=True, ollama_generate=overconfident, model="m",
    )
    assert res.confidence <= 0.95
    assert res.memory_influenced is True


@pytest.mark.asyncio
async def test_llm_failure_falls_back_with_memory_intact():
    async def boom(**kwargs):
        raise RuntimeError("llm timeout")

    rule = _rule([_hit(verified=True, similarity=0.95)])
    res = await synthesize(
        host="h1", health_score=10.0, anomalies=ANOMALIES, mirofish_frames=FRAMES,
        rule_result=rule, memory_hits=[_hit(verified=True, similarity=0.95)],
        use_llm=True, ollama_generate=boom, model="m",
    )
    assert res.method == "rule"
    assert res.memory_influenced is True


@pytest.mark.asyncio
async def test_llm_cannot_gain_confidence_from_unverified_memory():
    """Regression: the judge read the memory block and raised its own number
    from 0.92 to 0.97 off a case nobody had ever confirmed. That is the circular
    loop — the system's past guess making it surer of the same guess — so a
    citation of unverified memory can't buy confidence."""
    async def confident(**kwargs):
        return json.dumps({
            "root_cause_chain": ["x"], "confidence": 0.97, "fix_steps": ["y"],
            "reasoning": "z", "memory_refs": ["p1"],
        })

    rule = _rule()
    res = await synthesize(
        host="h1", health_score=10.0, anomalies=ANOMALIES, mirofish_frames=FRAMES,
        rule_result=rule,
        memory_hits=[_hit(point_id="p1", verified=False, similarity=0.98)],
        use_llm=True, ollama_generate=confident, model="m",
    )
    assert res.confidence == rule.confidence


@pytest.mark.asyncio
async def test_llm_keeps_its_confidence_when_memory_was_not_cited():
    """The cap is about memory-driven inflation only — other evidence (A2,
    propagation, its own reading of the anomalies) may still justify a high
    number."""
    async def confident(**kwargs):
        return json.dumps({
            "root_cause_chain": ["x"], "confidence": 0.9, "fix_steps": ["y"],
            "reasoning": "z", "memory_refs": [],
        })

    res = await synthesize(
        host="h1", health_score=10.0, anomalies=ANOMALIES, mirofish_frames=FRAMES,
        rule_result=_rule(), memory_hits=[_hit(point_id="p1", verified=False)],
        use_llm=True, ollama_generate=confident, model="m",
    )
    assert res.confidence == 0.9
