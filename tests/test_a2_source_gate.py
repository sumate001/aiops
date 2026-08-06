"""The AA judge must only see web research that is actually backed by sources.

When SearXNG returns nothing, Perplexica still produces a long answer written
from its own model's memory (it marks the text "[no source]"). Once that prose
is in the judge prompt it is indistinguishable from real evidence, so it has to
be withheld rather than laundered.
"""
import pytest

from app.models.response import PerplexicaEnrichment, PerplexicaSource
from app.routers import analyze

ANSWER = "MySQL deadlocks happen when two transactions wait on each other. [no source]"


def _state(enrichment):
    st = analyze._HostState(
        hostname="h1",
        entries=[],
        metric_samples=[],
        window_from="2026-05-21T10:00:00Z",
        window_to="2026-05-21T10:05:00Z",
        predict_result=None,
    )
    st.enrichment = enrichment
    return st


@pytest.fixture
def captured(monkeypatch):
    """Capture what _phase5_aa_llm hands to the synthesizer."""
    seen = {}

    async def fake_synthesize(**kwargs):
        seen.update(kwargs)
        return analyze.synthesizer.SynthesisResult(
            top_frame="Database", root_cause_chain=[], fix_steps=[],
            confidence=0.5, method="rule",
        )

    monkeypatch.setattr(analyze.synthesizer, "synthesize", fake_synthesize)
    return seen


@pytest.mark.asyncio
async def test_answer_withheld_when_no_sources(captured):
    await analyze._phase5_aa_llm(_state(
        PerplexicaEnrichment(query="deadlock", answer=ANSWER, sources=[])
    ))
    assert captured["perplexica_answer"] is None


@pytest.mark.asyncio
async def test_answer_passed_through_when_sourced(captured):
    await analyze._phase5_aa_llm(_state(PerplexicaEnrichment(
        query="deadlock", answer=ANSWER,
        sources=[PerplexicaSource(title="MySQL deadlock", url="https://stackoverflow.com/q/1")],
    )))
    assert captured["perplexica_answer"] == ANSWER


@pytest.mark.asyncio
async def test_no_enrichment_at_all(captured):
    await analyze._phase5_aa_llm(_state(None))
    assert captured["perplexica_answer"] is None


@pytest.mark.asyncio
async def test_unsourced_answer_still_reaches_the_response(captured):
    """Withholding is about the judge prompt only — the UI should still show
    that A2 ran and came back with nothing, rather than silently dropping it."""
    st = _state(PerplexicaEnrichment(query="deadlock", answer=ANSWER, sources=[]))
    await analyze._phase5_aa_llm(st)
    assert st.enrichment is not None and st.enrichment.answer == ANSWER
