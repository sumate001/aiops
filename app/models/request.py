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


class AnalyzeRequest(BaseModel):
    request_id: str | None = None
    tenant_id: str
    window: TimeWindow
    entries: list[LogEntry] = Field(min_length=1)
