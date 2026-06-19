"""
Transform GodEyes native log format into the canonical AnalyzeRequest schema.

Key differences handled here:
  - severity_number: string → int
  - severity_text: syslog labels → OTEL labels ("err"→"error", "warning"→"warn", etc.)
  - timestamp: _time → time
  - message: message / _msg → msg (prefer clean "message" field)
  - host: fall back to hostname if host is missing or "?"
  - structured_data.*: flatten into fields dict
  - window: derived from min/max _time if not provided explicitly
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Syslog → OTEL severity text normalization
_SEVERITY_TEXT_MAP: dict[str, str] = {
    "emerg": "fatal",
    "emergency": "fatal",
    "alert": "fatal",
    "crit": "fatal",
    "critical": "fatal",
    "err": "error",
    "error": "error",
    "warn": "warn",
    "warning": "warn",
    "notice": "info",
    "info": "info",
    "debug": "debug",
    "trace": "trace",
}

# OTEL severity_number defaults per text (used when severity_number missing)
_SEVERITY_NUMBER_FALLBACK: dict[str, int] = {
    "fatal": 21,
    "error": 17,
    "warn": 13,
    "info": 9,
    "debug": 5,
    "trace": 1,
}

_STRUCTURED_DATA_PREFIX = re.compile(r"^structured_data\.")


def _normalize_severity_text(raw: str | None) -> str:
    if not raw:
        return "info"
    return _SEVERITY_TEXT_MAP.get(raw.strip().lower(), raw.strip().lower())


def _coerce_severity_number(raw: str | int | None, severity_text: str) -> int:
    if raw is None:
        return _SEVERITY_NUMBER_FALLBACK.get(severity_text, 9)
    try:
        return int(raw)
    except (ValueError, TypeError):
        return _SEVERITY_NUMBER_FALLBACK.get(severity_text, 9)


def _resolve_host(entry: dict[str, Any]) -> str:
    host = entry.get("host", "")
    if host and host not in ("?", "unknown", ""):
        return host
    hostname = entry.get("hostname", "")
    return hostname or "unknown"


def _resolve_time(entry: dict[str, Any]) -> str | None:
    """Return ISO timestamp string, trying _time then falling back to other fields."""
    for key in ("_time", "time", "timestamp", "EventReceivedTime"):
        val = entry.get(key)
        if val:
            return str(val)
    return None


def _resolve_message(entry: dict[str, Any]) -> str:
    """Prefer clean 'message' field; fall back to _msg (raw syslog)."""
    msg = entry.get("message", "").strip()
    if msg:
        return msg
    raw = entry.get("_msg", "").strip()
    # Strip syslog priority + header if present: <N>1 TIMESTAMP HOST APP PID ...
    if raw.startswith("<"):
        # Find last ] (end of structured data) and take what's after
        bracket_end = raw.rfind("] ")
        if bracket_end != -1:
            return raw[bracket_end + 2:].strip()
        # Simpler fallback: remove syslog header (first 6 space-separated tokens)
        parts = raw.split(" ", 6)
        if len(parts) >= 7:
            return parts[6].strip()
    return raw or "(empty)"


def _collect_extra_fields(entry: dict[str, Any]) -> dict[str, Any]:
    """Flatten structured_data.* and other extra fields into the fields dict."""
    known_keys = {
        "type", "asset_id", "hostname", "host", "service", "tenant_id",
        "_time", "_msg", "_stream", "_stream_id", "message",
        "severity_number", "severity_text", "version",
        "data_source_agent", "facility", "proc_id", "remote_ip",
        "scope.name", "scope.version", "service_profile", "criticality",
    }
    fields: dict[str, Any] = {}
    for k, v in entry.items():
        if k in known_keys:
            continue
        # structured_data.NXLOG@14506.EventID → EventID
        if _STRUCTURED_DATA_PREFIX.match(k):
            short = k.split(".", 2)[-1]  # drop "structured_data.NXLOG@14506."
            fields[short] = v
        else:
            fields[k] = v
    return fields


def transform_entry(raw: dict[str, Any], default_tenant_id: str = "internal") -> dict[str, Any] | None:
    """Convert a single GodEyes log entry dict to a canonical LogEntry dict.
    Returns None if the entry is missing critical fields (time or message)."""

    time_str = _resolve_time(raw)
    if not time_str:
        logger.debug("Skipping entry with no timestamp: %s", raw.get("asset_id"))
        return None

    severity_text_raw = raw.get("severity_text")
    severity_text = _normalize_severity_text(severity_text_raw)
    severity_number = _coerce_severity_number(raw.get("severity_number"), severity_text)

    return {
        "time": time_str,
        "host": _resolve_host(raw),
        "service": raw.get("service") or "unknown",
        "severity_text": severity_text,
        "severity_number": severity_number,
        "msg": _resolve_message(raw),
        "service_profile": raw.get("service_profile"),
        "criticality": raw.get("criticality"),
        "fields": _collect_extra_fields(raw),
    }


def derive_window(entries: list[dict[str, Any]]) -> tuple[str, str]:
    """Compute min/max _time from canonical entries (already have 'time' key)."""
    times: list[datetime] = []
    for e in entries:
        try:
            t_str = e.get("time", "")
            # Handle both Z and +00:00 suffixes
            t_str = t_str.replace("Z", "+00:00")
            times.append(datetime.fromisoformat(t_str))
        except Exception:
            continue

    if not times:
        now = datetime.now(tz=timezone.utc)
        return now.isoformat().replace("+00:00", "Z"), now.isoformat().replace("+00:00", "Z")

    min_t = min(times).isoformat().replace("+00:00", "Z")
    max_t = max(times).isoformat().replace("+00:00", "Z")
    return min_t, max_t


def build_analyze_request(
    raw_entries: list[dict[str, Any]],
    request_id: str | None = None,
    tenant_id: str = "internal",
    window_from: str | None = None,
    window_to: str | None = None,
) -> dict[str, Any]:
    """Convert a list of GodEyes raw log dicts into an AnalyzeRequest-compatible dict."""

    canonical: list[dict[str, Any]] = []
    skipped = 0
    for raw in raw_entries:
        # Only process log entries (skip metrics / export_meta)
        if raw.get("type") not in (None, "log"):
            continue
        tenant = raw.get("tenant_id") or tenant_id
        transformed = transform_entry(raw, default_tenant_id=tenant)
        if transformed is None:
            skipped += 1
            continue
        canonical.append(transformed)

    if skipped:
        logger.info("Skipped %d entries with missing timestamp", skipped)

    if not canonical:
        raise ValueError("No valid log entries after transformation")

    if window_from is None or window_to is None:
        derived_from, derived_to = derive_window(canonical)
        window_from = window_from or derived_from
        window_to = window_to or derived_to

    return {
        "request_id": request_id,
        "tenant_id": tenant_id,
        "window": {"from": window_from, "to": window_to},
        "entries": canonical,
    }
