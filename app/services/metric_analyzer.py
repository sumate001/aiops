"""Evaluate numeric metric samples against thresholds and emit AnomalyScore.

Metrics ride into aiops through the same /ingest channel as logs (entries with
type="metric"). Here we group a host's samples by metric name, compare the
latest value to the configured warn/critical thresholds, and turn breaches into
the existing AnomalyScore shape — which already carries current_value,
baseline_mean and predicted_breach_at. That folds metric anomalies straight
into the host health score, synthesizer, prediction and A2 enrichment.
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from app.config import config, MetricThreshold
from app.models.request import MetricSample
from app.models.response import AnomalyScore

logger = logging.getLogger(__name__)


def group_by_host(metrics: list[MetricSample]) -> dict[str, list[MetricSample]]:
    groups: dict[str, list[MetricSample]] = defaultdict(list)
    for m in metrics:
        groups[m.host].append(m)
    return dict(groups)


def _match_threshold(name: str) -> MetricThreshold | None:
    """Find a configured threshold whose key is a substring of the metric name.
    Prefer the longest (most specific) matching key."""
    lname = name.lower()
    best: tuple[int, MetricThreshold] | None = None
    for key, thr in config.analysis.metric_thresholds.items():
        if key.lower() in lname and (best is None or len(key) > best[0]):
            best = (len(key), thr)
    return best[1] if best else None


def _breached(value: float, thr: MetricThreshold) -> str | None:
    """Return "high" (critical), "medium" (warn) or None."""
    if thr.direction == "below":
        if value <= thr.critical:
            return "high"
        if value <= thr.warn:
            return "medium"
    else:  # above
        if value >= thr.critical:
            return "high"
        if value >= thr.warn:
            return "medium"
    return None


def _score(value: float, thr: MetricThreshold) -> float:
    """Normalize how far past warn the value is, into 0..1 (saturating at critical).
    Works for both directions: for "below" thresholds critical < warn, so the
    span and the distance-past-warn are computed with the sign flipped."""
    if thr.direction == "below":
        span = thr.warn - thr.critical
        past = thr.warn - value
    else:
        span = thr.critical - thr.warn
        past = value - thr.warn
    if span == 0:
        return 1.0
    ratio = past / span
    return round(min(1.0, max(0.0, 0.5 + 0.5 * ratio)), 3)


def _predict_breach_at(samples: list[MetricSample], thr: MetricThreshold) -> datetime | None:
    """Linear-fit the samples and estimate when they cross the critical line.
    Returns None unless trending toward the breach with a future ETA."""
    pts = [(s.time, s.value) for s in samples if s.time]
    if len(pts) < 3:
        return None
    pts.sort(key=lambda p: p[0])
    t0 = pts[0][0]
    xs = [(t - t0).total_seconds() for t, _ in pts]
    ys = [v for _, v in pts]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom
    intercept = mean_y - slope * mean_x

    # Need to be moving toward the threshold.
    if thr.direction == "above" and slope <= 0:
        return None
    if thr.direction == "below" and slope >= 0:
        return None

    x_cross = (thr.critical - intercept) / slope
    last_x = xs[-1]
    if x_cross <= last_x:
        return None  # already crossed (handled by current_value), not a forecast
    eta = t0 + timedelta(seconds=x_cross)
    if eta <= datetime.now(tz=timezone.utc):
        return None
    return eta


def evaluate_host(samples: list[MetricSample]) -> list[AnomalyScore]:
    """Turn a host's metric samples into AnomalyScore entries (one per breached metric)."""
    by_name: dict[str, list[MetricSample]] = defaultdict(list)
    for s in samples:
        by_name[s.name].append(s)

    anomalies: list[AnomalyScore] = []
    for name, series in by_name.items():
        thr = _match_threshold(name)
        if thr is None:
            continue
        series.sort(key=lambda s: s.time)
        current = series[-1].value
        severity = _breached(current, thr)
        if severity is None:
            continue
        baseline = round(sum(s.value for s in series) / len(series), 3)
        anomalies.append(AnomalyScore(
            metric=name,
            score=_score(current, thr),
            severity=severity,
            current_value=round(current, 3),
            baseline_mean=baseline,
            predicted_breach_at=_predict_breach_at(series, thr),
        ))
        logger.info("metric breach — %s=%.2f (%s, thr warn=%s crit=%s)",
                    name, current, severity, thr.warn, thr.critical)
    return anomalies
