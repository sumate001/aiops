"""A1b seasonal forecasting.

The model itself is exercised separately (it needs a 200MB download); these
tests pin the logic around it — breach arithmetic, the history gate, hourly
aggregation, and batching shape.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.services import baseline_store
from app.services.forecast import Forecaster, breach_of


# ── breach arithmetic ───────────────────────────────────────────────────────
def test_value_inside_band_is_not_a_breach():
    breach, _ = breach_of(actual=50, p10=10, p50=50, p90=90)
    assert breach is False


def test_value_above_band_breaches():
    breach, _ = breach_of(actual=200, p10=10, p50=50, p90=90)
    assert breach is True


def test_value_below_band_breaches():
    """A metric collapsing is as informative as one spiking — a host that
    suddenly logs nothing is usually broken, not healthy."""
    breach, _ = breach_of(actual=0, p10=10, p50=50, p90=90)
    assert breach is True


def test_magnitude_is_scaled_by_band_width():
    """Measured in band-widths so it compares across metrics: a host swinging
    0-500 and one sitting at 0-5 both read ~1.0 when they miss by their spread."""
    _, wide = breach_of(actual=600, p10=0, p50=250, p90=500)
    _, narrow = breach_of(actual=6, p10=0, p50=2.5, p90=5)
    assert wide == pytest.approx(narrow, abs=0.05)


def test_magnitude_grows_with_distance():
    _, near = breach_of(actual=100, p10=10, p50=50, p90=90)
    _, far = breach_of(actual=1000, p10=10, p50=50, p90=90)
    assert far > near


def test_degenerate_band_does_not_divide_by_zero():
    _, mag = breach_of(actual=5, p10=1.0, p50=1.0, p90=1.0)
    assert mag == mag and mag > 0      # finite, not NaN


# ── hourly aggregation ──────────────────────────────────────────────────────
@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "ws.db"
    monkeypatch.setattr(baseline_store, "DB_PATH", str(path))
    baseline_store.init_db()
    return path


def _insert(db, host, when, error_count=1, health=90.0):
    c = sqlite3.connect(db)
    c.execute(
        "INSERT INTO window_stats (host, tenant_id, window_from, window_to, entry_count,"
        " error_count, warn_count, error_rate, health_score) VALUES (?,?,?,?,?,?,?,?,?)",
        (host, "t", when.strftime("%Y-%m-%d %H:%M:%S"),
         (when + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"),
         100, error_count, 0, error_count / 100, health),
    )
    c.commit()
    c.close()


def test_counts_are_summed_within_the_hour(db):
    base = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
    for i in range(4):                      # four 5-minute windows, same hour
        _insert(db, "h1", base + timedelta(minutes=i * 5), error_count=3)
    series = baseline_store.get_hourly_series("h1", "error_count", hours=24)
    assert len(series) == 1
    assert series[0][1] == 12               # summed, not averaged


def test_scores_are_averaged_within_the_hour(db):
    base = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
    for i, h in enumerate([80.0, 60.0]):
        _insert(db, "h1", base + timedelta(minutes=i * 5), health=h)
    series = baseline_store.get_hourly_series("h1", "health_score", hours=24)
    assert series[0][1] == pytest.approx(70.0)


def test_series_is_oldest_first(db):
    """Chronos reads a series forward in time; reversed input forecasts the past."""
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    for h in range(5):
        _insert(db, "h1", now - timedelta(hours=h), error_count=h + 1)
    series = baseline_store.get_hourly_series("h1", "error_count", hours=24)
    assert [b for b, _ in series] == sorted(b for b, _ in series)


def test_anomaly_score_is_derived_from_health(db):
    """Not a stored column — inverted so that "up" means "worse" for every
    forecast metric alike."""
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    _insert(db, "h1", now, health=25.0)
    series = baseline_store.get_hourly_series("h1", "anomaly_score", hours=24)
    assert series[0][1] == pytest.approx(0.75)


def test_unknown_metric_is_rejected(db):
    with pytest.raises(ValueError):
        baseline_store.get_hourly_series("h1", "definitely_not_a_column", hours=24)


def test_empty_history_returns_empty(db):
    assert baseline_store.get_hourly_series("ghost-host", "error_count", hours=24) == []


# ── batching ────────────────────────────────────────────────────────────────
def test_empty_batch_short_circuits():
    """No hosts had enough history — that's the normal early state, not a fault."""
    assert Forecaster().predict([], prediction_length=12) == []


def test_prediction_length_stays_within_what_the_model_was_trained_for():
    from app.config import config
    assert config.forecast.prediction_length <= 64


def test_quantiles_do_not_exceed_what_chronos_learned():
    from app.services.forecast import QUANTILES
    assert max(QUANTILES) <= 0.9 and min(QUANTILES) >= 0.1


# ── the history gate ────────────────────────────────────────────────────────
def test_min_history_covers_enough_daily_cycles():
    """Measured, not guessed. On a host spiking every 02:00, forecasting the next
    02:00 *with the spike present as usual*:
        3 days (the plan's 72h) → p90 21.5 vs actual 40 → BREACH
        5 days                  → p90 41.2 → no breach
        7 days                  → p90 43.4 → no breach
    Three cycles let the model notice the spike but not size it, so 72h produces
    exactly the false positive A1b exists to remove."""
    from app.config import config
    assert config.forecast.min_history_hours >= 120
