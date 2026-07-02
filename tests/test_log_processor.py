from datetime import datetime, timezone
from app.services.log_processor import (
    compute_host_health_score,
    compute_top_errors,
    filter_entries,
    group_by_host,
    score_to_status,
    compute_overall_health_score,
)
from app.models.request import LogEntry
from app.models.response import HostAnalysis


def _entry(host="h1", severity_number=17, msg="err", service_profile=None, criticality=None):
    return LogEntry(
        time=datetime(2026, 5, 21, 10, 0, 0, tzinfo=timezone.utc),
        host=host,
        service="svc",
        severity_text="error",
        severity_number=severity_number,
        msg=msg,
        service_profile=service_profile,
        criticality=criticality,
    )


class TestFilterEntries:
    def test_filters_below_threshold(self):
        entries = [_entry(severity_number=9), _entry(severity_number=13)]
        result = filter_entries(entries)
        assert len(result) == 1
        assert result[0].severity_number == 13

    def test_caps_at_max_entries(self, monkeypatch):
        from app.services import log_processor
        monkeypatch.setattr(log_processor.config.analysis, "max_log_entries", 3)
        entries = [_entry(severity_number=17, msg=f"e{i}") for i in range(5)]
        result = filter_entries(entries)
        assert len(result) == 3
        # keeps last 3
        assert result[0].msg == "e2"

    def test_accepts_all_within_limit(self):
        entries = [_entry(severity_number=17), _entry(severity_number=21)]
        result = filter_entries(entries)
        assert len(result) == 2


class TestGroupByHost:
    def test_groups_correctly(self):
        entries = [_entry(host="a"), _entry(host="b"), _entry(host="a")]
        groups = group_by_host(entries)
        assert len(groups["a"]) == 2
        assert len(groups["b"]) == 1


class TestComputeTopErrors:
    def test_top_5_by_count(self):
        entries = (
            [_entry(severity_number=17, msg="err-A")] * 5
            + [_entry(severity_number=17, msg="err-B")] * 3
            + [_entry(severity_number=17, msg="err-C")] * 1
            + [_entry(severity_number=13, msg="warn-D")] * 10  # warn — excluded from top_errors
        )
        tops = compute_top_errors(entries)
        assert tops[0].msg == "err-A"
        assert tops[0].count == 5
        assert all(t.msg != "warn-D" for t in tops)

    def test_limit(self):
        entries = [_entry(severity_number=17, msg=f"e{i}") for i in range(10)]
        assert len(compute_top_errors(entries)) == 5


class TestHealthScore:
    def test_perfect_score_no_errors(self):
        score = compute_host_health_score(0, 0, 100, [])
        assert score == 100.0

    def test_all_errors_deduction(self):
        # 100 errors, 100 entries — error deduction is capped at 70 so a
        # pre-filtered (error-only) stream can't zero the score by construction
        score = compute_host_health_score(100, 0, 100, [])
        assert score == 30.0

    def test_anomaly_penalty(self):
        score = compute_host_health_score(0, 0, 100, [1.0])
        assert score == 70.0  # 100 - 0 - 30

    def test_mixed(self):
        # 10 errors, 10 warns, 100 entries, weights 2.0/1.0
        # deduction = (10*2 + 10*1) / 100 * 100 = 30
        score = compute_host_health_score(10, 10, 100, [])
        assert score == 70.0


class TestScoreToStatus:
    def test_ok(self):
        assert score_to_status(100) == "ok"
        assert score_to_status(70) == "ok"

    def test_warning(self):
        assert score_to_status(69) == "warning"
        assert score_to_status(40) == "warning"

    def test_critical(self):
        assert score_to_status(39) == "critical"
        assert score_to_status(0) == "critical"


class TestOverallHealthScore:
    def _host(self, host, criticality, score):
        return HostAnalysis(
            host=host,
            criticality=criticality,
            entry_count=10,
            error_count=0,
            warn_count=0,
            health_score=score,
            status=score_to_status(score),
        )

    def test_weighted_by_criticality(self):
        gold = self._host("g", "gold", 0.0)    # weight 3
        bronze = self._host("b", "bronze", 100.0)  # weight 1
        # (0*3 + 100*1) / 4 = 25.0
        result = compute_overall_health_score([gold, bronze])
        assert result == 25.0

    def test_empty_returns_100(self):
        assert compute_overall_health_score([]) == 100.0
