"""
Predictive engine — ใช้ POS Knowledge Base (app/knowledge/pos.py)

ขั้น 2 Anomaly Detection  : Spike / Drift / Pattern
ขั้น 4 Failure Fingerprint: match กับ POS failure scenarios
ขั้น 5 Noise Reduction    : suppress false alert จาก routine patterns
"""

import math
import logging
from datetime import datetime

from app.models.response import TrendInfo, PredictionInfo
from app.services.baseline_store import get_recent_windows, get_same_hour_baseline
from app.knowledge.pos import (
    POS_NOISE_PATTERNS,
    check_noise,
    extract_signals_from_messages,
    match_fingerprint,
)

logger = logging.getLogger(__name__)


# ─── helpers ─────────────────────────────────────────────────────────────────

def _ts(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _linear_slope(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    sx, sy = sum(xs), sum(ys)
    sxy = sum(x * y for x, y in zip(xs, ys))
    sxx = sum(x * x for x in xs)
    denom = n * sxx - sx * sx
    return (n * sxy - sx * sy) / denom if denom != 0 else 0.0


def _weighted_slope(xs: list[float], ys: list[float], decay: float = 0.9) -> float:
    """Exponentially recency-weighted least-squares slope — the most recent
    window gets weight 1.0, each older window decays by `decay`."""
    n = len(xs)
    if n < 2:
        return 0.0
    weights = [decay ** (n - 1 - i) for i in range(n)]
    sw = sum(weights)
    wx = sum(w * x for w, x in zip(weights, xs))
    wy = sum(w * y for w, y in zip(weights, ys))
    mean_x, mean_y = wx / sw, wy / sw
    num = sum(w * (x - mean_x) * (y - mean_y) for w, x, y in zip(weights, xs, ys))
    den = sum(w * (x - mean_x) ** 2 for w, x in zip(weights, xs))
    return num / den if den != 0 else 0.0


def _std(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return math.sqrt(sum((v - mean) ** 2 for v in values) / n)


def extract_signals(top_error_msgs: list[str]) -> dict[str, int]:
    """ใช้ POS signal patterns จับ signal จาก log messages"""
    return extract_signals_from_messages(top_error_msgs)


# ─── ขั้น 2: Anomaly Detection ───────────────────────────────────────────────

def analyze_trend(host: str) -> TrendInfo:
    windows = get_recent_windows(host, limit=15)
    n = len(windows)

    if n < 3:
        return TrendInfo(direction="unknown", slope_per_hour=0.0, windows_analyzed=n)

    windows = sorted(windows, key=lambda w: w["window_from"])

    t0 = _ts(windows[0]["window_from"])
    xs           = [(_ts(w["window_from"]) - t0) / 3600 for w in windows]
    error_rates  = [w["error_rate"]   for w in windows]
    health_scores= [w["health_score"] for w in windows]

    slope = _weighted_slope(xs, error_rates)

    # Adaptive threshold: noisy hosts naturally have a higher std(error_rate),
    # so a fixed 0.02 flags them as "rising" too often. Floor still applies for
    # very quiet hosts where even a small slope is meaningful.
    SLOPE_THRESH = max(0.02, 1.5 * _std(error_rates))
    if slope > SLOPE_THRESH:
        direction = "rising"
    elif slope < -SLOPE_THRESH:
        direction = "falling"
    else:
        direction = "stable"

    anomaly_types: list[str] = []

    # Spike: last window > 2× previous AND meaningful
    if n >= 2 and error_rates[-1] > error_rates[-2] * 2 and error_rates[-1] > 0.1:
        anomaly_types.append("spike")

    # Drift: consistent upward slope over ≥5 windows
    if n >= 5 and direction == "rising":
        anomaly_types.append("drift")

    # Pattern (ขั้น 2 แบบที่ 3): หลาย signal ผิดพร้อมกัน
    recent = windows[-3:]
    crash_recent    = sum(w.get("crash_count", 0) for w in recent)
    auth_recent     = sum(w.get("auth_fail_count", 0) for w in recent)
    payment_recent  = sum(w.get("payment_fail_count", 0) for w in recent)
    network_recent  = sum(w.get("network_err_count", 0) for w in recent)
    db_recent       = sum(w.get("db_err_count", 0) for w in recent)
    hardware_recent = sum(w.get("hardware_err_count", 0) for w in recent)

    signals_on = sum([
        error_rates[-1] > 0.3,
        crash_recent > 0,
        auth_recent > 0,
        payment_recent > 0,
        network_recent > 0,
        db_recent > 0,
        hardware_recent > 0,
        direction == "rising",
    ])
    if signals_on >= 3:
        anomaly_types.append("pattern")

    # Baseline comparison (ขั้น 1 time-aware)
    z_score = None
    baseline_comparison = None
    try:
        dt = datetime.fromisoformat(windows[-1]["window_from"].replace("Z", "+00:00"))
        day_type = "weekend" if dt.weekday() >= 5 else "weekday"
        baseline = get_same_hour_baseline(host, dt.hour, day_type)
        if baseline and baseline.get("avg_error_rate") is not None and baseline["sample_count"] >= 3:
            avg = baseline["avg_error_rate"] or 0
            var = max(baseline.get("var_error_rate") or 0, 0)
            std = math.sqrt(var) if var > 0 else 0.01
            z_score = (error_rates[-1] - avg) / std
            if avg > 0:
                ratio = error_rates[-1] / avg
                baseline_comparison = (
                    f"{ratio:.1f}× above baseline" if ratio > 1.1 else
                    f"{ratio:.1f}× below baseline" if ratio < 0.9 else
                    "near baseline"
                )
            if abs(z_score) > 3:
                anomaly_types.append("baseline_deviation")
    except Exception as exc:
        logger.debug("Baseline comparison failed for %s: %s", host, exc)

    return TrendInfo(
        direction=direction,
        slope_per_hour=round(slope, 4),
        windows_analyzed=n,
        baseline_comparison=baseline_comparison,
        z_score=round(z_score, 2) if z_score is not None else None,
        anomaly_types=list(set(anomaly_types)),
    )


# ─── ขั้น 4+5: Fingerprint matching + Noise Reduction ────────────────────────

def generate_prediction(
    host: str,
    current_health: float,
    trend: TrendInfo,
    error_count: int,
    warn_count: int,
    entry_count: int,
    anomalies: list[dict] | None = None,
    top_error_msgs: list[str] | None = None,
) -> PredictionInfo:

    anomalies = anomalies or []
    top_error_msgs = top_error_msgs or []
    signals: list[str] = []
    risk = 0

    # ── A1 anomalies (rule + IF) ─────────────────────────────────────────────
    if_anomalies = [a for a in anomalies if a.get("metric") == "isolation_forest"]
    if if_anomalies:
        a = if_anomalies[0]
        signals.append(f"A1-IF flagged: score={a['score']:.2f} severity={a['severity']}")
        risk += 15 if a["severity"] == "high" else 8

    # ── ขั้น 5: Noise check ──────────────────────────────────────────────────
    noise_reduction = 0
    noise_reason: str | None = None
    windows_all = get_recent_windows(host, limit=5)
    if windows_all:
        try:
            latest_win = sorted(windows_all, key=lambda w: w["window_from"])[-1]
            dt = datetime.fromisoformat(latest_win["window_from"].replace("Z", "+00:00"))
            noise = check_noise(top_error_msgs, dt.hour, dt.day)
            if noise:
                noise_reduction = noise["risk_reduction"]
                noise_reason = noise["reason"]
                signals.append(f"ℹ️  Possible noise: {noise_reason}")
        except Exception:
            pass

    # ── สถานะปัจจุบัน ────────────────────────────────────────────────────────
    if current_health < 40:
        signals.append(f"Health score critical: {current_health:.1f}/100")
        risk += 35
    elif current_health < 70:
        signals.append(f"Health score degraded: {current_health:.1f}/100")
        risk += 15

    # ── Trend (ขั้น 2) ────────────────────────────────────────────────────────
    if trend.direction == "rising":
        signals.append(f"Error rate rising ({trend.slope_per_hour * 100:+.1f}%/hr)")
        risk += 25
    elif trend.direction == "falling":
        signals.append("Error rate recovering ↓")
        risk -= 10

    if "spike" in trend.anomaly_types:
        signals.append("Sudden error spike detected")
        risk += 15
    if "drift" in trend.anomaly_types:
        signals.append("Gradual drift — possible resource leak (memory/connection pool)")
        risk += 20
    if "pattern" in trend.anomaly_types:
        signals.append("Multi-signal anomaly: several error types active simultaneously")
        risk += 25
    if "baseline_deviation" in trend.anomaly_types and trend.z_score:
        signals.append(f"Z-score {trend.z_score:.1f}σ from time-aware baseline ({trend.baseline_comparison})")
        risk += 10

    # ── Error rate ────────────────────────────────────────────────────────────
    err_rate = error_count / entry_count if entry_count else 0
    if err_rate > 0.5:
        signals.append(f"Error rate {err_rate * 100:.0f}% of log volume (KPI: danger >50%)")
        risk += 10

    # ── ขั้น 4: POS Failure Fingerprint matching ──────────────────────────────
    # รวม signal counts จาก recent windows
    all_signal_counts: dict[str, int] = {
        "crash": 0, "auth_fail": 0, "payment_fail": 0,
        "network_err": 0, "db_err": 0, "hardware_err": 0, "app_crash": 0,
    }
    for w in windows_all:
        all_signal_counts["crash"]        += w.get("crash_count", 0)
        all_signal_counts["auth_fail"]    += w.get("auth_fail_count", 0)
        all_signal_counts["payment_fail"] += w.get("payment_fail_count", 0)
        all_signal_counts["network_err"]  += w.get("network_err_count", 0)
        all_signal_counts["db_err"]       += w.get("db_err_count", 0)
        all_signal_counts["hardware_err"] += w.get("hardware_err_count", 0)
        all_signal_counts["app_crash"]    += w.get("app_crash_count", 0)

    # เพิ่ม signals จาก messages ปัจจุบัน (อาจจะมีมากกว่าที่เก็บใน DB)
    current_signals = extract_signals_from_messages(top_error_msgs)
    for k, v in current_signals.items():
        all_signal_counts[k] = max(all_signal_counts.get(k, 0), v)

    matched_fp = match_fingerprint(all_signal_counts)
    matched_fingerprint: str | None = None

    if matched_fp:
        matched_fingerprint = matched_fp["name"]
        signals.append(f"⚠ Matches POS fingerprint: {matched_fp['name']} — {matched_fp['description']}")

        # Proportional score: a fingerprint with only its required signal active
        # shouldn't score the same as one where every signal (required +
        # supporting) fired.
        fp_signals = matched_fp["required_signals"] + matched_fp["supporting_signals"]
        active = [s for s in fp_signals if all_signal_counts.get(s, 0) > 0]
        active_ratio = len(active) / len(fp_signals) if fp_signals else 0.0
        risk += round(20 * active_ratio)

        if active:
            signals.append(f"  Active signals: {', '.join(active)}")

    # ── Apply noise reduction ─────────────────────────────────────────────────
    if noise_reduction > 0:
        risk -= noise_reduction

    risk = max(0, min(100, risk))

    # ── Confidence ───────────────────────────────────────────────────────────
    window_factor     = min(trend.windows_analyzed / 10, 1.0)
    multisig_bonus    = 0.10 if "pattern" in trend.anomaly_types else 0.0
    fingerprint_bonus = 0.12 if matched_fp else 0.0
    noise_penalty     = -0.10 if noise_reduction > 0 else 0.0
    confidence = round(
        min(0.95, (risk / 100) * 0.63 + window_factor * 0.20
            + multisig_bonus + fingerprint_bonus + noise_penalty + 0.05),
        2,
    )

    if risk >= 70:
        risk_level = "critical"
    elif risk >= 45:
        risk_level = "high"
    elif risk >= 20:
        risk_level = "medium"
    else:
        risk_level = "low"

    # ── Estimated time to incident ───────────────────────────────────────────
    estimated_incident_in: str | None = None

    # ถ้า match fingerprint → ใช้ lead time จาก KB
    if matched_fp and risk >= 45:
        lead = matched_fp["lead_time_minutes"]
        estimated_incident_in = f"~{lead} min (historical lead time)"
    elif len(windows_all) >= 3 and trend.slope_per_hour > 0:
        sorted_w = sorted(windows_all, key=lambda w: w["window_from"])
        t0 = _ts(sorted_w[0]["window_from"])
        xs_h = [(_ts(w["window_from"]) - t0) / 3600 for w in sorted_w]
        hs   = [w["health_score"] for w in sorted_w]
        health_slope = _linear_slope(xs_h, hs)

        # Acceleration guard: cascading failures don't degrade linearly — if the
        # last 3 windows are falling >2x steeper than the overall trend, use
        # that steeper (more urgent) slope for the ETA instead.
        if len(sorted_w) >= 3:
            recent_slope = _linear_slope(xs_h[-3:], hs[-3:])
            if recent_slope < 0 and health_slope < 0 and abs(recent_slope) > abs(health_slope) * 2:
                health_slope = recent_slope

        if health_slope < -1.0:
            target = 40.0 if current_health > 40 else 0.0
            hrs = (current_health - target) / abs(health_slope)
            if 0 < hrs < 48:
                estimated_incident_in = (
                    f"~{max(1, int(hrs * 60))} min" if hrs < 1 else f"~{hrs:.1f} hr"
                )

    recs = {
        "critical": "🚨 Immediate action required — escalate to on-call now.",
        "high":     "⚠️  Investigate within 15 minutes to prevent service disruption.",
        "medium":   "🔍 Monitor closely and prepare runbook for potential incident.",
        "low":      "✅ No immediate action needed — continue monitoring.",
    }

    return PredictionInfo(
        risk_level=risk_level,
        risk_score=float(risk),
        self_confidence=confidence,
        estimated_incident_in=estimated_incident_in,
        contributing_signals=signals,
        recommendation=recs[risk_level],
        matched_fingerprint=matched_fingerprint,
    )
