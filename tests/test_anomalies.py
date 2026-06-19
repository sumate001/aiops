"""Quick smoke tests — no pytest fixtures needed."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["ML_MODEL_STORE"] = "/tmp/log_ml_test_models"
os.environ["ML_MIN_WINDOWS"] = "5"   # ลดเหลือ 5 สำหรับ test

from app.models.schemas import WindowStatInput
from app.services.features import extract, to_vector, rule_based_score
from app.services.forest import train, score_windows


def _make_window(error_rate: float = 0.0, health: float = 100.0, **kwargs) -> WindowStatInput:
    entry = 100
    return WindowStatInput(
        window_from="2026-06-19T08:00:00Z",
        window_to="2026-06-19T08:05:00Z",
        entry_count=entry,
        error_count=int(entry * error_rate),
        warn_count=0,
        health_score=health,
        **kwargs,
    )


def test_feature_extraction():
    w = _make_window(error_rate=0.5, health=30.0, db_err_count=10)
    f = extract(w)
    assert abs(f["error_rate"] - 0.5) < 0.01
    assert abs(f["health_score_norm"] - 0.30) < 0.01
    assert abs(f["db_err_rate"] - 0.10) < 0.01
    print("✓ feature extraction")


def test_rule_based_anomaly():
    normal = _make_window(error_rate=0.05, health=90.0)
    anomaly = _make_window(error_rate=0.8, health=10.0, crash_count=2)

    _, is_normal = rule_based_score(normal)
    _, is_anomaly = rule_based_score(anomaly)

    assert not is_normal, "normal window should not be anomaly"
    assert is_anomaly, "critical window should be anomaly"
    print("✓ rule-based fallback")


def test_train_and_score():
    normal_windows = [_make_window(error_rate=0.02, health=95.0) for _ in range(6)]
    status, n = train("test", "host-a", normal_windows)
    assert status == "trained"
    assert n == 6

    test_windows = [
        _make_window(error_rate=0.02, health=95.0),   # normal
        _make_window(error_rate=0.95, health=5.0, crash_count=5),  # anomaly
    ]
    results, model_trained, _ = score_windows("test", "host-a", test_windows)
    assert model_trained
    assert results[0].method == "isolation_forest"
    print(f"✓ IF trained & scored — normal={results[0].anomaly_label}, spike={results[1].anomaly_label}")


def test_fallback_without_model():
    results, model_trained, _ = score_windows("test", "no-model-host", [
        _make_window(error_rate=0.01, health=99.0)
    ])
    assert not model_trained
    assert results[0].method == "rule_based"
    print("✓ rule-based fallback when no model")


if __name__ == "__main__":
    test_feature_extraction()
    test_rule_based_anomaly()
    test_train_and_score()
    test_fallback_without_model()
    print("\nAll tests passed.")
