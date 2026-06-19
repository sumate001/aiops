import pytest
from datetime import datetime, timezone
from pydantic import ValidationError

from app.models.request import AnalyzeRequest, LogEntry, TimeWindow


def _base_entry():
    return {
        "time": "2026-05-21T10:00:00Z",
        "host": "h1",
        "service": "svc",
        "severity_text": "error",
        "severity_number": 17,
        "msg": "something failed",
    }


class TestAnalyzeRequest:
    def test_valid_request(self):
        req = AnalyzeRequest(
            tenant_id="internal",
            window={"from": "2026-05-21T10:00:00Z", "to": "2026-05-21T10:05:00Z"},
            entries=[_base_entry()],
        )
        assert req.tenant_id == "internal"
        assert len(req.entries) == 1

    def test_request_id_optional(self):
        req = AnalyzeRequest(
            tenant_id="t1",
            window={"from": "2026-05-21T10:00:00Z", "to": "2026-05-21T10:05:00Z"},
            entries=[_base_entry()],
        )
        assert req.request_id is None

    def test_empty_entries_raises(self):
        with pytest.raises(ValidationError):
            AnalyzeRequest(
                tenant_id="t1",
                window={"from": "2026-05-21T10:00:00Z", "to": "2026-05-21T10:05:00Z"},
                entries=[],
            )

    def test_missing_tenant_id_raises(self):
        with pytest.raises(ValidationError):
            AnalyzeRequest(
                window={"from": "2026-05-21T10:00:00Z", "to": "2026-05-21T10:05:00Z"},
                entries=[_base_entry()],
            )

    def test_optional_fields_default(self):
        entry = LogEntry(**_base_entry())
        assert entry.service_profile is None
        assert entry.criticality is None
        assert entry.fields == {}

    def test_time_window_alias(self):
        w = TimeWindow(**{"from": "2026-05-21T10:00:00Z", "to": "2026-05-21T10:05:00Z"})
        assert w.from_.year == 2026
