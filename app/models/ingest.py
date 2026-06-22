from pydantic import BaseModel, Field
from typing import Any


class GodEyesLogEntry(BaseModel):
    """Raw log entry from GodEyes export / webhook push."""

    model_config = {"extra": "allow"}

    # timestamp — GodEyes uses _time (RFC3339)
    _time: str | None = None

    # message fields
    message: str | None = None
    _msg: str | None = None  # raw syslog, fallback

    # host identity
    host: str | None = None
    hostname: str | None = None  # GodEyes asset name (different from host!)

    service: str | None = None

    severity_number: str | int | None = None
    severity_text: str | None = None

    tenant_id: str | None = None
    asset_id: str | None = None

    # optional enrichment (may be set by aiops-ctl before push)
    service_profile: str | None = None
    criticality: str | None = None


class GodEyesIngestRequest(BaseModel):
    """Batch of GodEyes log entries sent to POST /ingest."""

    request_id: str | None = None
    tenant_id: str | None = None  # fallback if not in each entry
    asset_id: str | None = None

    # explicit window override; if None → derived from min/max _time in entries
    window_from: str | None = Field(None, alias="window_from")
    window_to: str | None = Field(None, alias="window_to")

    # optional: POST AnalyzeResponse back to this URL after analysis
    callback_url: str | None = None

    entries: list[dict[str, Any]] = Field(min_length=1)
