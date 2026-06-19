from app.services.ollama import parse_json_response


class TestParseJsonResponse:
    def test_clean_json(self):
        text = '{"summary": "ok", "likely_causes": [], "affected_metrics": [], "suggested_actions": []}'
        result = parse_json_response(text)
        assert result["summary"] == "ok"

    def test_strips_markdown_fences(self):
        text = '```json\n{"summary": "test", "likely_causes": [], "affected_metrics": [], "suggested_actions": []}\n```'
        result = parse_json_response(text)
        assert result["summary"] == "test"

    def test_fallback_on_invalid_json(self):
        text = "This is plain text from the LLM."
        result = parse_json_response(text)
        assert result["summary"].startswith("This is plain text")
        assert result["likely_causes"] == []
        assert result["affected_metrics"] == []
        assert result["suggested_actions"] == []

    def test_truncates_long_plain_text(self):
        text = "x" * 1000
        result = parse_json_response(text)
        assert len(result["summary"]) == 500
