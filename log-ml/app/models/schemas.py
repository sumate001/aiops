from pydantic import BaseModel, Field
from typing import Any


class WindowStatInput(BaseModel):
    """9-feature window snapshot — mirrors log-analyzer's WindowStat"""
    window_from: str
    window_to: str
    entry_count: int = 0
    error_count: int = 0
    warn_count: int = 0
    health_score: float = 100.0
    crash_count: int = 0
    auth_fail_count: int = 0
    payment_fail_count: int = 0
    network_err_count: int = 0
    db_err_count: int = 0
    hardware_err_count: int = 0
    app_crash_count: int = 0


class AnomalyRequest(BaseModel):
    host: str
    tenant_id: str = "internal"
    windows: list[WindowStatInput] = Field(min_length=1)


class WindowAnomalyResult(BaseModel):
    window_from: str
    window_to: str
    anomaly_score: float        # IF raw score: negative = more anomalous
    is_anomaly: bool
    anomaly_label: str          # "anomaly" | "normal"
    method: str                 # "isolation_forest" | "rule_based"
    features: dict[str, float]


class AnomalyResponse(BaseModel):
    model_config = {"protected_namespaces": ()}

    host: str
    tenant_id: str
    model_trained: bool
    n_train_samples: int
    min_windows_for_if: int
    results: list[WindowAnomalyResult]


class TrainRequest(BaseModel):
    host: str
    tenant_id: str = "internal"
    windows: list[WindowStatInput] = Field(min_length=1)


class TrainResponse(BaseModel):
    host: str
    tenant_id: str
    status: str                 # "trained" | "skipped" | "updated"
    n_samples: int
    message: str


class ModelMeta(BaseModel):
    host: str
    tenant_id: str
    n_samples: int
    trained_at: str
    features: list[str]
    contamination: float


class ModelsResponse(BaseModel):
    model_config = {"protected_namespaces": ()}

    models: list[ModelMeta]
    model_store: str
