"""
A2 — Perplexica client for external knowledge enrichment
ใช้ quality optimization mode เสมอ เพื่อผลลัพธ์ที่แม่นยำที่สุด
Graceful fallback: ถ้า Perplexica ไม่ up → return None
"""

import logging
import httpx

logger = logging.getLogger(__name__)


def build_query(
    top_frame: str | None,
    top_keywords: list[str],
    top_error_msgs: list[str],
    host: str,
) -> str:
    parts: list[str] = []

    if top_frame:
        parts.append(f"POS system {top_frame.lower()} issue")

    if top_keywords:
        parts.append(" ".join(top_keywords[:3]))

    if top_error_msgs:
        msg = top_error_msgs[0][:120]
        parts.append(f'"{msg}"')

    if not parts:
        parts.append(f"POS server {host} troubleshooting")

    return " ".join(parts) + " root cause fix"


async def search(
    query: str,
    base_url: str = "http://localhost:3001",
    timeout: float = 30.0,
) -> dict | None:
    """
    Query Perplexica API with quality optimization mode.
    Returns: {"answer": str, "sources": list[dict]} or None
    """
    payload = {
        "chatModel": {
            "provider": "ollama",
            "model": "qwen2.5:14b",
        },
        "embeddingModel": {
            "provider": "ollama",
            "model": "nomic-embed-text",
        },
        "optimizationMode": "quality",
        "focusMode": "webSearch",
        "query": query,
        "history": [],
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{base_url}/api/search", json=payload)
            resp.raise_for_status()
            data = resp.json()
            answer = data.get("message", "")
            sources = [
                {
                    "title": s.get("metadata", {}).get("title", ""),
                    "url": s.get("metadata", {}).get("url", ""),
                }
                for s in data.get("sources", [])
                if s.get("metadata", {}).get("url")
            ]
            return {
                "answer": answer[:2000],
                "sources": sources[:5],
                "query": query,
            }
    except httpx.ConnectError:
        logger.debug("Perplexica not reachable at %s", base_url)
    except Exception as exc:
        logger.warning("Perplexica error: %s", exc)
    return None
