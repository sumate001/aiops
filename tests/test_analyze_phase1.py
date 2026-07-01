from datetime import datetime, timezone

import pytest

from app.models.request import LogEntry
from app.routers import analyze
from app.services.baseline_store import WindowStat


def _entry(severity_number=17, msg="err"):
    return LogEntry(
        time=datetime(2026, 5, 21, 10, 0, 0, tzinfo=timezone.utc),
        host="h1",
        service="svc",
        severity_text="error",
        severity_number=severity_number,
        msg=msg,
    )


@pytest.mark.asyncio
async def test_save_window_stat_reflects_post_if_health_score(monkeypatch):
    """WindowStat persisted must use health_score after IF anomaly penalty,
    not the pre-IF value (P4 fix)."""

    monkeypatch.setattr(analyze.config.log_ml, "enabled", True)

    async def fake_score_window(**kwargs):
        return {"anomaly_score": -0.9, "is_anomaly": True, "method": "if", "features": {}}

    monkeypatch.setattr(analyze.log_ml_client, "score_window", fake_score_window)
    monkeypatch.setattr(analyze, "analyze_trend", lambda host: "stable")
    monkeypatch.setattr(analyze, "generate_prediction", lambda **kwargs: None)

    saved: list[WindowStat] = []
    monkeypatch.setattr(analyze, "save_window_stat", lambda stat: saved.append(stat))

    st = analyze._HostState(
        hostname="h1",
        entries=[_entry()] + [_entry(severity_number=9, msg="info") for _ in range(20)],
        metric_samples=[],
        window_from="2026-05-21T10:00:00Z",
        window_to="2026-05-21T10:05:00Z",
        predict_result=None,
    )

    await analyze._phase1_a1(st)

    assert len(saved) == 1
    pre_if_health_score = analyze.compute_host_health_score(
        st.error_count, st.warn_count, len(st.entries), []
    )
    assert st.health_score < pre_if_health_score
    assert saved[0].health_score == st.health_score
