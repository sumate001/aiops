from pydantic import BaseModel
from datetime import datetime


class TrendInfo(BaseModel):
    direction: str                          # rising | falling | stable | unknown
    slope_per_hour: float                   # error_rate change per hour
    windows_analyzed: int
    baseline_comparison: str | None = None  # "2.3× above baseline"
    z_score: float | None = None
    anomaly_types: list[str] = []           # spike | drift | pattern | baseline_deviation


class PredictionInfo(BaseModel):
    risk_level: str                         # low | medium | high | critical
    risk_score: float = 0.0                 # 0-100 raw risk score behind risk_level
    self_confidence: float                  # 0.0–1.0 — predictor's confidence in its own risk estimate
                                             # (NOT overall confidence — see Synthesis.confidence for that)
    estimated_incident_in: str | None = None
    contributing_signals: list[str] = []
    recommendation: str = ""
    matched_fingerprint: str | None = None  # ชื่อ failure pattern ที่ match


class AnomalyScore(BaseModel):
    metric: str
    score: float
    severity: str
    current_value: float | None = None
    baseline_mean: float | None = None
    predicted_breach_at: datetime | None = None


class MiroFishFrame(BaseModel):
    frame: str
    lens: str
    relevance: float
    signal_hits: int
    keyword_hits: int
    top_keywords: list[str] = []
    insight: str | None = None


class PerplexicaSource(BaseModel):
    title: str
    url: str


class PerplexicaEnrichment(BaseModel):
    query: str
    answer: str
    sources: list[PerplexicaSource] = []


class Synthesis(BaseModel):
    root_cause_chain: list[str]
    confidence: float
    fix_steps: list[str]
    method: str
    top_frame: str | None = None
    top_frame_lens: str | None = None
    anomaly_methods: list[str] = []
    reasoning: str | None = None


class TopError(BaseModel):
    msg: str
    count: int
    first_seen: datetime
    last_seen: datetime


class Explanation(BaseModel):
    summary: str
    likely_causes: list[str] = []
    affected_metrics: list[str] = []
    suggested_actions: list[str] = []


class HostAnalysis(BaseModel):
    host: str
    service_profile: str | None = None
    criticality: str | None = None
    entry_count: int
    error_count: int
    warn_count: int
    health_score: float
    status: str
    anomalies: list[AnomalyScore] = []
    top_errors: list[TopError] = []
    explanation: Explanation | None = None
    trend: TrendInfo | None = None
    prediction: PredictionInfo | None = None
    mirofish: list[MiroFishFrame] = []
    synthesis: Synthesis | None = None
    enrichment: PerplexicaEnrichment | None = None


class Sources(BaseModel):
    aiops_ml_used: bool
    ollama_used: bool
    ollama_model: str


class AnalyzeResponse(BaseModel):
    request_id: str | None = None
    tenant_id: str
    window: dict
    analyzed_at: datetime
    health_score: float
    status: str
    hosts: list[HostAnalysis]
    summary: str
    sources: Sources
