"""
Extract 9-feature vector จาก WindowStatInput สำหรับ Isolation Forest

Features:
  1. error_rate          — errors / total entries
  2. warn_rate           — warns / total entries
  3. health_score_norm   — health_score / 100
  4. crash_rate          — crash_count / total
  5. auth_fail_rate      — auth_fail_count / total
  6. payment_fail_rate   — payment_fail_count / total
  7. network_err_rate    — network_err_count / total
  8. db_err_rate         — db_err_count / total
  9. app_crash_rate      — (hardware_err_count + app_crash_count) / total
"""

from app.models.schemas import WindowStatInput

FEATURE_NAMES = [
    "error_rate",
    "warn_rate",
    "health_score_norm",
    "crash_rate",
    "auth_fail_rate",
    "payment_fail_rate",
    "network_err_rate",
    "db_err_rate",
    "app_crash_rate",
]


def extract(w: WindowStatInput) -> dict[str, float]:
    n = max(w.entry_count, 1)
    return {
        "error_rate":        w.error_count / n,
        "warn_rate":         w.warn_count / n,
        "health_score_norm": w.health_score / 100.0,
        "crash_rate":        w.crash_count / n,
        "auth_fail_rate":    w.auth_fail_count / n,
        "payment_fail_rate": w.payment_fail_count / n,
        "network_err_rate":  w.network_err_count / n,
        "db_err_rate":       w.db_err_count / n,
        "app_crash_rate":    (w.hardware_err_count + w.app_crash_count) / n,
    }


def to_vector(w: WindowStatInput) -> list[float]:
    f = extract(w)
    return [f[name] for name in FEATURE_NAMES]


def rule_based_score(w: WindowStatInput) -> tuple[float, bool]:
    """Fallback เมื่อ IF model ยังไม่พร้อม — คืน (score, is_anomaly)"""
    f = extract(w)
    score = 0.0
    score -= f["error_rate"] * 0.4
    score -= (1.0 - f["health_score_norm"]) * 0.3
    score -= f["crash_rate"] * 0.1
    score -= f["auth_fail_rate"] * 0.05
    score -= f["payment_fail_rate"] * 0.05
    score -= f["db_err_rate"] * 0.05
    score -= f["app_crash_rate"] * 0.05
    # normalize: IF range คือ [-0.5, 0.5] ประมาณ
    score = max(-0.5, min(0.5, score))
    is_anomaly = (
        f["error_rate"] > 0.3
        or f["health_score_norm"] < 0.4
        or f["crash_rate"] > 0
        or f["app_crash_rate"] > 0
    )
    return score, is_anomaly
