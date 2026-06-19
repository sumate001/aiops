import json
import logging

import httpx

from app.knowledge.pos import POS_OLLAMA_CONTEXT

logger = logging.getLogger(__name__)


class OllamaError(Exception):
    pass


async def generate(
    prompt: str,
    model: str,
    base_url: str,
    timeout: float = 120.0,
    temperature: float = 0.1,
) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.post(f"{base_url}/api/generate", json=payload)
            resp.raise_for_status()
            body = resp.json()
            return body.get("response", "")
        except httpx.ConnectError as exc:
            raise OllamaError(f"ollama unreachable: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise OllamaError(f"ollama timeout: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise OllamaError(f"ollama error {exc.response.status_code}: {exc}") from exc


def parse_json_response(text: str) -> dict:
    clean = text.strip()
    if clean.startswith("```"):
        lines = clean.split("\n")
        clean = "\n".join(lines[1:-1])
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        return {
            "summary": text[:500],
            "likely_causes": [],
            "affected_metrics": [],
            "suggested_actions": [],
        }


def build_analysis_prompt(
    hostname: str,
    service_profile: str | None,
    criticality: str | None,
    window_from: str,
    window_to: str,
    top_errors: list[dict],
    anomalies: list[dict],
) -> str:
    top_errors_lines = "\n".join(
        f"  - {e['msg']}: {e['count']}" for e in top_errors
    ) or "  (none)"

    anomalies_lines = "\n".join(
        f"  - {a['metric']}: score={a['score']:.2f} severity={a['severity']}"
        for a in anomalies
    ) or "  (none)"

    return f"""{POS_OLLAMA_CONTEXT}

---

Analyze the following log data and return a JSON object ONLY (no markdown, no explanation outside the JSON).

Host: {hostname}
Profile: {service_profile or 'unknown'}
Criticality: {criticality or 'unknown'}
Time window: {window_from} to {window_to}

Top errors (message: count):
{top_errors_lines}

Anomaly scores (from ML model):
{anomalies_lines}

Return JSON with exactly these keys:
{{
  "summary": "one sentence describing the main issue in context of POS system",
  "likely_causes": ["cause 1 (reference POS failure scenario if applicable)", "cause 2"],
  "affected_metrics": ["metric1", "metric2"],
  "suggested_actions": ["action 1", "action 2", "action 3"]
}}"""
