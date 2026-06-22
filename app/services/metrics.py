"""
Prometheus metrics สำหรับ log-analyzer
อัปเดตหลังทุก /analyze call
"""

from prometheus_client import Gauge, Counter, Histogram, CollectorRegistry, REGISTRY

# ── Health & Status ────────────────────────────────────────────────────────────
host_health_score = Gauge(
    "godeyes_host_health_score",
    "Health score per host (0-100)",
    ["host", "tenant_id"],
)

host_status = Gauge(
    "godeyes_host_status",
    "Host status encoded: ok=1, warning=2, critical=3",
    ["host", "tenant_id"],
)

overall_health_score = Gauge(
    "godeyes_overall_health_score",
    "Overall health score across all hosts (0-100)",
    ["tenant_id"],
)

# ── Log volume ─────────────────────────────────────────────────────────────────
host_error_count = Gauge(
    "godeyes_host_error_count",
    "Error log count per host per window",
    ["host", "tenant_id"],
)

host_warn_count = Gauge(
    "godeyes_host_warn_count",
    "Warning log count per host per window",
    ["host", "tenant_id"],
)

host_entry_count = Gauge(
    "godeyes_host_entry_count",
    "Total log entries per host per window",
    ["host", "tenant_id"],
)

# ── Anomaly detection ──────────────────────────────────────────────────────────
host_anomaly_count = Gauge(
    "godeyes_host_anomaly_count",
    "Number of anomalies detected per host",
    ["host", "tenant_id"],
)

host_anomaly_score = Gauge(
    "godeyes_host_anomaly_score",
    "Max anomaly score per host (0-1)",
    ["host", "tenant_id"],
)

# ── MiroFish frame relevance ───────────────────────────────────────────────────
mirofish_frame_relevance = Gauge(
    "godeyes_mirofish_frame_relevance",
    "MiroFish frame relevance score (0-1)",
    ["host", "tenant_id", "frame"],
)

# ── AA Synthesis ──────────────────────────────────────────────────────────────
synthesis_confidence = Gauge(
    "godeyes_synthesis_confidence",
    "AA Synthesizer confidence score (0-1)",
    ["host", "tenant_id", "top_frame"],
)

# ── Request counters ───────────────────────────────────────────────────────────
analyze_requests_total = Counter(
    "godeyes_analyze_requests_total",
    "Total /analyze requests",
)

analyze_errors_total = Counter(
    "godeyes_analyze_errors_total",
    "Total /analyze errors",
)

analyze_duration_seconds = Histogram(
    "godeyes_analyze_duration_seconds",
    "Duration of /analyze requests",
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0],
)

# ── Status encoder ─────────────────────────────────────────────────────────────
_STATUS_CODE = {"ok": 1, "warning": 2, "critical": 3}


def record_analysis(tenant_id: str, hosts: list, overall_score: float, overall_st: str) -> None:
    """อัปเดต Prometheus metrics หลังทุก /analyze"""
    overall_health_score.labels(tenant_id=tenant_id).set(overall_score)

    for h in hosts:
        host = h.host
        labels = {"host": host, "tenant_id": tenant_id}

        host_health_score.labels(**labels).set(h.health_score)
        host_status.labels(**labels).set(_STATUS_CODE.get(h.status, 0))
        host_error_count.labels(**labels).set(h.error_count)
        host_warn_count.labels(**labels).set(h.warn_count)
        host_entry_count.labels(**labels).set(h.entry_count)

        host_anomaly_count.labels(**labels).set(len(h.anomalies))
        max_score = max((a.score for a in h.anomalies), default=0.0)
        host_anomaly_score.labels(**labels).set(max_score)

        for frame in h.mirofish:
            mirofish_frame_relevance.labels(
                host=host, tenant_id=tenant_id, frame=frame.frame
            ).set(frame.relevance)

        if h.synthesis:
            synthesis_confidence.labels(
                host=host,
                tenant_id=tenant_id,
                top_frame=h.synthesis.top_frame or "none",
            ).set(h.synthesis.confidence)
