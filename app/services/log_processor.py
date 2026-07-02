import re
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

    # No error-level messages: fall back to warn-level ones so downstream
    # consumers (MiroFish, LLM judge, A2 query) still see the actual log text —
    # on a warn-only stream these are the only evidence there is.
    if not counts:
        for e in entries:
            if WARN_MIN <= e.severity_number <= WARN_MAX:
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
    anomaly_penalty = max(anomaly_scores, default=0.0) * 30

    if entry_count == 0:
        # No log entries (e.g. metrics-only host): score solely on anomalies.
        score = cfg.score_ceiling - anomaly_penalty
        return round(max(cfg.score_floor, min(cfg.score_ceiling, score)), 2)

    # Cap each severity's contribution: ingest streams are often pre-filtered
    # to warn+ (e.g. GodEye), so the ratio saturates at 100% by construction.
    # Warnings alone can only take a host into "warning" territory (-35);
    # errors can push it to critical (-70). Anomalies stack on top.
    error_ded = min(70.0, error_count * cfg.critical_weight / entry_count * 100)
    warn_ded = min(35.0, warn_count * cfg.warn_weight / entry_count * 100)
    score = cfg.score_ceiling - error_ded - warn_ded - anomaly_penalty
    return round(max(cfg.score_floor, min(cfg.score_ceiling, score)), 2)


def score_to_status(score: float) -> str:
    if score >= 70:
        return "ok"
    if score >= 40:
        return "warning"
    return "critical"


# Status ordering, worst-last — used to take the worse of two statuses.
_STATUS_RANK = {"ok": 0, "warning": 1, "critical": 2}


def worse_status(a: str, b: str) -> str:
    return a if _STATUS_RANK.get(a, 0) >= _STATUS_RANK.get(b, 0) else b


def escalate_status(base_status: str, anomalies: list) -> str:
    """Best practice: a breached threshold drives status directly, regardless of
    the (averaged) health score. A `high`/`critical` anomaly floors the host at
    critical; a `medium`/`warn` one floors it at warning. Prevents alert
    dilution where one severe metric is washed out by an otherwise calm host."""
    severities = {getattr(a, "severity", "") for a in anomalies}
    if {"high", "critical"} & severities:
        return worse_status(base_status, "critical")
    if {"medium", "warn"} & severities:
        return worse_status(base_status, "warning")
    return base_status


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


# Markdown constructs that render as noise in the GodEye UI (plain text).
_MD_NOISE = re.compile(
    r"""```.*?```          # fenced code blocks
      | ^\s{0,3}\#{1,6}\s* # heading markers
      | ^\s*\|.*\|\s*$     # table rows
      | ^\s*[-=*_]{3,}\s*$ # horizontal rules
      | \*\*|__|`          # bold/emphasis/inline-code markers
      | \[(\d+)\]          # citation refs like [1]
    """,
    re.MULTILINE | re.DOTALL | re.VERBOSE,
)


def _plain_excerpt(text: str, max_sentences: int = 3, max_chars: int = 400) -> str:
    """Strip markdown and squeeze whitespace, keeping only the lead sentences —
    for UI surfaces that render plain text."""
    text = _MD_NOISE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    sentences = re.split(r"(?<=[.!?])\s+", text)
    excerpt = " ".join(sentences[:max_sentences])
    if len(excerpt) > max_chars:
        excerpt = excerpt[:max_chars].rsplit(" ", 1)[0] + "…"
    return excerpt


def _describe_primary(primary: HostAnalysis, fallback_msg: str) -> str:
    """Short, plain-text briefing on the primary host, preferring the AA
    Synthesizer's LLM/rule root-cause chain and reasoning (specific,
    evidence-based) over the raw top error message (a symptom, not a cause).
    Rendered with newlines — the GodEye UI shows this as plain text, so no
    markdown and no wall-of-text. Full detail stays in synthesis/enrichment."""
    criticality_label = f" ({primary.criticality} tier)" if primary.criticality else ""
    sr = primary.synthesis

    if sr and sr.root_cause_chain:
        # The GodEye UI collapses newlines, so keep everything inline:
        # numbered chain steps and " | " between sections.
        chain = "  ".join(f"{i}) {step}" for i, step in enumerate(sr.root_cause_chain, 1))
        parts = [f"Primary issue on {primary.host}{criticality_label}: {chain}"]
        if sr.reasoning:
            parts.append(f"Why: {_plain_excerpt(sr.reasoning, max_sentences=2)}")
        if primary.enrichment and primary.enrichment.answer:
            parts.append(f"Research: {_plain_excerpt(primary.enrichment.answer, max_sentences=2, max_chars=300)}")
        if sr.fix_steps:
            parts.append(f"Recommended first step: {sr.fix_steps[0]}")
        return " | ".join(parts)

    return f"Primary issue: {fallback_msg} on {primary.host}{criticality_label}."


def build_summary(host_analyses: list[HostAnalysis]) -> str:
    critical = [h for h in host_analyses if h.status == "critical"]
    warning = [h for h in host_analyses if h.status == "warning"]
    total = len(host_analyses)

    base = f"{len(critical)} critical, {len(warning)} warning, {total - len(critical) - len(warning)} ok hosts out of {total}."

    if critical:
        primary = critical[0]
        top_msg = primary.top_errors[0].msg if primary.top_errors else "unknown issue"
        base += " | " + _describe_primary(primary, top_msg)
    elif warning:
        primary = warning[0]
        top_msg = primary.top_errors[0].msg if primary.top_errors else "degraded performance"
        base += " | " + _describe_primary(primary, top_msg)

    return base
