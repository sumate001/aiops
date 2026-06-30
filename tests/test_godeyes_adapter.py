import pytest
from app.services.godeyes_adapter import (
    transform_entry,
    _normalize_severity_text,
    _coerce_severity_number,
    _resolve_host,
    _resolve_message,
    _collect_extra_fields,
    build_analyze_request,
    derive_window,
)


SAMPLE_LOG = {
    "type": "log",
    "asset_id": "38067732",
    "hostname": "DEV_Hyper_Store_Bigc",
    "_time": "2026-05-20T14:21:46Z",
    "host": "POCPOS2008R2.bigc.co.th",
    "service": "MSSQLSERVER",
    "severity_number": "17",
    "severity_text": "err",
    "message": "Login failed for user 'sa'.",
    "_msg": "<11>1 2026-05-20T21:21:46+07:00 POCPOS2008R2.bigc.co.th MSSQLSERVER - - [] Login failed for user 'sa'.",
    "tenant_id": "internal",
    "structured_data.NXLOG@14506.EventID": "18456",
    "structured_data.NXLOG@14506.EventType": "AUDIT_FAILURE",
}


class TestSeverityNormalization:
    def test_err_maps_to_error(self):
        assert _normalize_severity_text("err") == "error"

    def test_warning_maps_to_warn(self):
        assert _normalize_severity_text("warning") == "warn"

    def test_warn_maps_to_warn(self):
        assert _normalize_severity_text("warn") == "warn"

    def test_notice_maps_to_info(self):
        assert _normalize_severity_text("notice") == "info"

    def test_case_insensitive(self):
        assert _normalize_severity_text("ERR") == "error"

    def test_none_returns_info(self):
        assert _normalize_severity_text(None) == "info"

    def test_unknown_passthrough(self):
        assert _normalize_severity_text("emerg") == "fatal"


class TestSeverityNumberCoercion:
    def test_string_to_int(self):
        assert _coerce_severity_number("17", "error") == 17

    def test_int_passthrough(self):
        assert _coerce_severity_number(13, "warn") == 13

    def test_none_fallback_from_text(self):
        assert _coerce_severity_number(None, "error") == 17
        assert _coerce_severity_number(None, "warn") == 13

    def test_invalid_string_fallback(self):
        assert _coerce_severity_number("bogus", "info") == 9


class TestResolveHost:
    def test_uses_host_field(self):
        assert _resolve_host({"host": "myserver.local"}) == "myserver.local"

    def test_falls_back_to_hostname_when_question_mark(self):
        assert _resolve_host({"host": "?", "hostname": "DEV_Asset"}) == "DEV_Asset"

    def test_falls_back_to_hostname_when_missing(self):
        assert _resolve_host({"hostname": "DEV_Asset"}) == "DEV_Asset"

    def test_returns_unknown_when_both_missing(self):
        assert _resolve_host({}) == "unknown"


class TestResolveMessage:
    def test_prefers_clean_message_field(self):
        entry = {"message": "Clean message", "_msg": "<11>1 raw syslog"}
        assert _resolve_message(entry) == "Clean message"

    def test_falls_back_to_msg_stripped(self):
        entry = {"_msg": "<11>1 2026-01-01T00:00:00Z host svc 0 - [] The actual message"}
        result = _resolve_message(entry)
        assert "The actual message" in result

    def test_empty_returns_placeholder(self):
        assert _resolve_message({}) == "(empty)"


class TestCollectExtraFields:
    def test_strips_structured_data_prefix(self):
        entry = {
            "structured_data.NXLOG@14506.EventID": "18456",
            "structured_data.NXLOG@14506.EventType": "AUDIT_FAILURE",
        }
        fields = _collect_extra_fields(entry)
        assert fields["EventID"] == "18456"
        assert fields["EventType"] == "AUDIT_FAILURE"

    def test_excludes_known_keys(self):
        entry = {"host": "h1", "severity_number": "17", "unknown_extra": "val"}
        fields = _collect_extra_fields(entry)
        assert "host" not in fields
        assert "severity_number" not in fields
        assert fields["unknown_extra"] == "val"


class TestTransformEntry:
    def test_transforms_sample_log(self):
        result = transform_entry(SAMPLE_LOG)
        assert result is not None
        assert result["time"] == "2026-05-20T14:21:46Z"
        assert result["host"] == "POCPOS2008R2.bigc.co.th"
        assert result["service"] == "MSSQLSERVER"
        assert result["severity_number"] == 17
        assert result["severity_text"] == "error"
        assert result["msg"] == "Login failed for user 'sa'."
        assert result["fields"]["EventID"] == "18456"

    def test_skips_entry_without_time(self):
        entry = {**SAMPLE_LOG}
        del entry["_time"]
        assert transform_entry(entry) is None

    def test_host_fallback_for_question_mark(self):
        entry = {**SAMPLE_LOG, "host": "?"}
        result = transform_entry(entry)
        assert result["host"] == "DEV_Hyper_Store_Bigc"


class TestBuildAnalyzeRequest:
    def test_builds_valid_request(self):
        result = build_analyze_request([SAMPLE_LOG], tenant_id="internal")
        assert result["tenant_id"] == "internal"
        assert len(result["entries"]) == 1
        assert result["window"]["from"] == result["window"]["to"]  # single entry

    def test_skips_invalid_metric_types(self):
        metric_entry = {"type": "metric", "asset_id": "123"}  # no time/name/value
        result = build_analyze_request([SAMPLE_LOG, metric_entry])
        assert len(result["entries"]) == 1
        assert result["metrics"] == []

    def test_extracts_valid_metrics(self):
        metric = {
            "type": "metric", "_time": "2026-05-20T10:00:00Z",
            "host": "pos-db-01", "metric": "cpu_usage", "value": 92.5, "unit": "percent",
            "region": "th",
        }
        result = build_analyze_request([SAMPLE_LOG, metric])
        assert len(result["entries"]) == 1
        assert len(result["metrics"]) == 1
        m = result["metrics"][0]
        assert m["name"] == "cpu_usage"
        assert m["value"] == 92.5
        assert m["host"] == "pos-db-01"
        assert m["labels"]["region"] == "th"

    def test_metrics_only_request(self):
        metric = {
            "type": "metric", "_time": "2026-05-20T10:00:00Z",
            "host": "pos-db-01", "name": "memory_usage", "value": 88,
        }
        result = build_analyze_request([metric])
        assert result["entries"] == []
        assert len(result["metrics"]) == 1

    def test_raises_when_no_valid_entries(self):
        with pytest.raises(ValueError, match="No valid log or metric entries"):
            build_analyze_request([{"type": "metric"}])

    def test_explicit_window_overrides_derived(self):
        result = build_analyze_request(
            [SAMPLE_LOG],
            window_from="2026-05-20T00:00:00Z",
            window_to="2026-05-20T23:59:59Z",
        )
        assert result["window"]["from"] == "2026-05-20T00:00:00Z"
        assert result["window"]["to"] == "2026-05-20T23:59:59Z"

    def test_derives_window_from_entries(self):
        early = {**SAMPLE_LOG, "_time": "2026-05-20T10:00:00Z"}
        late = {**SAMPLE_LOG, "_time": "2026-05-20T10:05:00Z"}
        result = build_analyze_request([early, late])
        assert "10:00:00" in result["window"]["from"]
        assert "10:05:00" in result["window"]["to"]
