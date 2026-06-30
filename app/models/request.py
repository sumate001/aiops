from pydantic import BaseModel, Field
from datetime import datetime
from typing import Any


class TimeWindow(BaseModel):
    from_: datetime = Field(alias="from")
    to: datetime

    model_config = {"populate_by_name": True}


class LogEntry(BaseModel):
    time: datetime
    host: str
    service: str
    severity_text: str
    severity_number: int
    msg: str
    service_profile: str | None = None
    criticality: str | None = None
    fields: dict[str, Any] = {}


class MetricSample(BaseModel):
    """A single numeric metric sample (e.g. cpu_usage=92.5) from GodEye."""
    time: datetime
    host: str
    name: str
    value: float
    unit: str | None = None
    service: str | None = None
    service_profile: str | None = None
    criticality: str | None = None
    labels: dict[str, Any] = {}


class AnalyzeRequest(BaseModel):
    request_id: str | None = None
    tenant_id: str
    window: TimeWindow
    # At least one of entries/metrics must be present (validated in the router).
    entries: list[LogEntry] = []
    metrics: list[MetricSample] = []
