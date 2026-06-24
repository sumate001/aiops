"""
Isolation Forest wrapper — train, score, persist per host
Model files: model_store/{tenant_id}/{host}.joblib + .meta.json
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

from app.models.schemas import WindowStatInput, WindowAnomalyResult, ModelMeta
from app.services.features import FEATURE_NAMES, extract, to_vector, rule_based_score

logger = logging.getLogger(__name__)

MODEL_STORE = os.environ.get("ML_MODEL_STORE", "model_store")
MIN_WINDOWS = int(os.environ.get("ML_MIN_WINDOWS", "30"))
CONTAMINATION = float(os.environ.get("ML_CONTAMINATION", "0.05"))


def _model_dir(tenant_id: str) -> Path:
    p = Path(MODEL_STORE) / tenant_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def _model_path(tenant_id: str, host: str) -> Path:
    safe = host.replace("/", "_").replace(":", "_")
    return _model_dir(tenant_id) / f"{safe}.joblib"


def _meta_path(tenant_id: str, host: str) -> Path:
    safe = host.replace("/", "_").replace(":", "_")
    return _model_dir(tenant_id) / f"{safe}.meta.json"


def load_model(tenant_id: str, host: str) -> IsolationForest | None:
    p = _model_path(tenant_id, host)
    if not p.exists():
        return None
    try:
        return joblib.load(p)
    except Exception as exc:
        logger.warning("Failed to load model for %s/%s: %s", tenant_id, host, exc)
        return None


def save_model(model: IsolationForest, tenant_id: str, host: str, n_samples: int) -> None:
    joblib.dump(model, _model_path(tenant_id, host))
    meta = {
        "host": host,
        "tenant_id": tenant_id,
        "n_samples": n_samples,
        "trained_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "features": FEATURE_NAMES,
        "contamination": CONTAMINATION,
    }
    _meta_path(tenant_id, host).write_text(json.dumps(meta, indent=2))
    logger.info("Model saved: %s/%s (%d samples)", tenant_id, host, n_samples)


def train(tenant_id: str, host: str, windows: list[WindowStatInput]) -> tuple[str, int]:
    """Fit or update IF model. Returns (status, n_samples)."""
    n = len(windows)
    if n < MIN_WINDOWS:
        return "skipped", n

    X = np.array([to_vector(w) for w in windows])
    model = IsolationForest(
        n_estimators=100,
        contamination=CONTAMINATION,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X)
    existing = load_model(tenant_id, host)
    save_model(model, tenant_id, host, n)
    return "updated" if existing else "trained", n


def score_windows(
    tenant_id: str,
    host: str,
    windows: list[WindowStatInput],
) -> tuple[list[WindowAnomalyResult], bool, int]:
    """Score windows with IF (or fallback). Returns (results, model_trained, n_train_samples)."""
    model = load_model(tenant_id, host)
    model_trained = model is not None

    meta_p = _meta_path(tenant_id, host)
    n_train = 0
    if meta_p.exists():
        try:
            n_train = json.loads(meta_p.read_text()).get("n_samples", 0)
        except Exception:
            pass

    results: list[WindowAnomalyResult] = []

    if model_trained:
        X = np.array([to_vector(w) for w in windows])
        raw_scores = model.score_samples(X)   # negative = anomaly
        labels = model.predict(X)             # -1 = anomaly, 1 = normal
        threshold = model.offset_

        for w, score, label in zip(windows, raw_scores, labels):
            features = extract(w)
            results.append(WindowAnomalyResult(
                window_from=w.window_from,
                window_to=w.window_to,
                anomaly_score=round(float(score), 4),
                is_anomaly=bool(label == -1),
                anomaly_label="anomaly" if label == -1 else "normal",
                method="isolation_forest",
                features={k: round(v, 4) for k, v in features.items()},
            ))
    else:
        for w in windows:
            score, is_anomaly = rule_based_score(w)
            features = extract(w)
            results.append(WindowAnomalyResult(
                window_from=w.window_from,
                window_to=w.window_to,
                anomaly_score=round(score, 4),
                is_anomaly=is_anomaly,
                anomaly_label="anomaly" if is_anomaly else "normal",
                method="rule_based",
                features={k: round(v, 4) for k, v in features.items()},
            ))

    return results, model_trained, n_train


def list_models() -> list[ModelMeta]:
    store = Path(MODEL_STORE)
    if not store.exists():
        return []
    metas = []
    for meta_file in store.rglob("*.meta.json"):
        try:
            data = json.loads(meta_file.read_text())
            metas.append(ModelMeta(**data))
        except Exception:
            pass
    return sorted(metas, key=lambda m: m.trained_at, reverse=True)
