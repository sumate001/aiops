from collections import defaultdict
from datetime import datetime

from app.config import config, SEVERITY_THRESHOLD
from app.models.request import LogEntry, AnalyzeRequest
from app.models.response import TopError, HostAnalysis


CRITICALITY_MULTIPLIER: dict[str, int] = {"gold": 3, "silver": 2, "bronze": 1}

# OTEL severity ranges
WARN_MIN, WARN_MAX = 13, 16
ERROR_MIN = 17


def _severity_threshold() -> int:
    return SEVERITY_THRESHOLD.get(config.analysis.severity_filter, SEVERITY_THRESHOLD["warn"])


def filter_entries(entries: list[LogEntry]) -> list[LogEntry]:
    threshold = _severity_threshold()
    filtered = [e for e in entries if e.severity_number >= threshold]
    max_entries = config.analysis.max_log_entries
    if len(filtered) > max_entries:
        filtered = filtered[-max_entries:]
    return filtered


def group_by_host(entries: list[LogEntry]) -> dict[str, list[LogEntry]]:
    groups: dict[str, list[LogEntry]] = defaultdict(list)
    for entry in entries:
        groups[entry.host].append(entry)
    return dict(groups)


def compute_top_errors(entries: list[LogEntry], limit: int = 5) -> list[TopError]:
    counts: dict[str, list[datetime]] = defaultdict(list)
    for e in entries:
        if e.severity_number >= ERROR_MIN:
            counts[e.msg].append(e.time)

    top = sorted(counts.items(), key=lambda x: len(x[1]), reverse=True)[:limit]
    return [
        TopError(
            msg=msg,
            count=len(times),
            first_seen=min(times),
            last_seen=max(times),
        )
        for msg, times in top
    ]


def compute_host_health_score(
    error_count: int,
    warn_count: int,
    entry_count: int,
    anomaly_scores: list[float],
) -> float:
    cfg = config.analysis.health_score
    if entry_count == 0:
        return cfg.score_ceiling

    deduction = (
        (error_count * cfg.critical_weight + warn_count * cfg.warn_weight) / entry_count * 100
    )
    anomaly_penalty = max(anomaly_scores, default=0.0) * 30
    score = cfg.score_ceiling - deduction - anomaly_penalty
    return round(max(cfg.score_floor, min(cfg.score_ceiling, score)), 2)


def score_to_status(score: float) -> str:
    if score >= 70:
        return "ok"
    if score >= 40:
        return "warning"
    return "critical"


def compute_overall_health_score(host_analyses: list[HostAnalysis]) -> float:
    if not host_analyses:
        return 100.0
    weighted_sum = 0.0
    weight_total = 0
    for ha in host_analyses:
        multiplier = CRITICALITY_MULTIPLIER.get(ha.criticality or "bronze", 1)
        weighted_sum += ha.health_score * multiplier
        weight_total += multiplier
    return round(weighted_sum / weight_total, 2)


def build_summary(host_analyses: list[HostAnalysis]) -> str:
    critical = [h for h in host_analyses if h.status == "critical"]
    warning = [h for h in host_analyses if h.status == "warning"]
    total = len(host_analyses)

    base = f"{len(critical)} critical, {len(warning)} warning, {total - len(critical) - len(warning)} ok hosts out of {total}."

    if critical:
        primary = critical[0]
        top_msg = primary.top_errors[0].msg if primary.top_errors else "unknown issue"
        criticality_label = f" ({primary.criticality} tier)" if primary.criticality else ""
        base += f" Primary issue: {top_msg} on {primary.host}{criticality_label}."
    elif warning:
        primary = warning[0]
        top_msg = primary.top_errors[0].msg if primary.top_errors else "degraded performance"
        base += f" Primary issue: {top_msg} on {primary.host}."

    return base
