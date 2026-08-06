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
    memory_refs: list[str] = []       # point_id ที่ AA อ้างถึงจริง
    memory_influenced: bool = False   # AA ใช้ memory ในการสรุปไหม
    playbook_refs: list[str] = []     # playbook id ที่ AA อ้างถึง
    playbook_influenced: bool = False


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


class ForecastBand(BaseModel):
    """A1b — what this host's own history says to expect right now.

    A metric inside the band is normal behaviour *for this host at this hour*,
    however large the absolute value looks. `breach_magnitude` is measured in
    band-widths, so it stays comparable between a host that swings 0-500 and one
    that sits at 0-5.
    """
    metric: str
    p10: float
    p50: float
    p90: float
    actual: float | None = None
    breach: bool = False
    breach_magnitude: float = 0.0


class MemoryHit(BaseModel):
    """One recalled case (A4). Two scores, deliberately kept apart:

    `similarity` is the raw cosine from Qdrant and is the only one allowed to
    decide confidence. `final_score` is weighted for ranking and can exceed 1.0
    — using it for confidence would let a barely-relevant verified case look
    like strong evidence.
    """
    point_id: str
    kind: str = "analysis"          # analysis | playbook
    similarity: float               # raw — drives confidence
    final_score: float              # weighted — drives ordering only
    symptom_text: str
    root_cause_chain: list[str] = []
    fix_steps: list[str] = []
    verified: bool = False
    actual_fix: str | None = None
    occurrence_count: int = 0
    created_at: str = ""
    days_ago: int = 0
    # playbook-only
    title: str | None = None
    verify_steps: list[str] = []
    docs_url: str | None = None


class HostAnalysis(BaseModel):
    host: str
    # Database engine inferred from the log text (mysql | postgresql | mongodb),
    # None when it couldn't be told apart. Filters A4's memory/playbook search.
    detected_service: str | None = None
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
    memory_hits: list[MemoryHit] = []
    forecast: list[ForecastBand] = []


class PropagationIncident(BaseModel):
    node_id: str
    label: str | None = None
    start_health: float
    end_health: float
    warn_at_minute: int | None = None
    crit_at_minute: int | None = None
    caused_by: list[str] = []              # chain ต้นตอ → node นี้
    trigger: str = "propagation"           # "trend" = พยากรณ์จากแนวโน้มตัวเอง


class PropagationForecast(BaseModel):
    """Phase 3 — ผลจาก graph propagation engine (deterministic)
    field ใหม่แยกจาก prediction เดิม เพื่อไม่กระทบ GodEye UI"""
    engine: str
    horizon_minutes: int
    seeded: dict[str, float] = {}          # node_id → health เริ่มต้นจากผลวิเคราะห์จริง
    incidents: list[PropagationIncident] = []


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
    propagation_forecast: PropagationForecast | None = None
