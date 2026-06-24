"""
A2 — Perplexica client for external knowledge enrichment
ดึง Ollama provider UUID จาก /api/providers แล้วใช้ใน /api/search
"""

import asyncio
import logging
import httpx

logger = logging.getLogger(__name__)

_provider_cache: dict[str, str] = {}   # base_url → provider UUID


async def _get_ollama_provider_id(base_url: str, client: httpx.AsyncClient) -> str | None:
    cached = _provider_cache.get(base_url)
    if cached:
        return cached
    try:
        r = await client.get(f"{base_url}/api/providers")
        r.raise_for_status()
        providers = r.json().get("providers", [])
        for p in providers:
            if p.get("type") == "ollama" or "ollama" in p.get("name", "").lower():
                _provider_cache[base_url] = p["id"]
                return p["id"]
    except Exception as exc:
        logger.debug("Failed to fetch Perplexica providers: %s", exc)
    return None


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
        parts.append(f'"{top_error_msgs[0][:120]}"')
    if not parts:
        parts.append(f"POS server {host} troubleshooting")
    return " ".join(parts) + " root cause fix"


async def _do_search(
    query: str,
    base_url: str,
    chat_model: str,
    embedding_model: str,
    timeout: float,
) -> dict | None:
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10, read=timeout, write=30)) as client:
        provider_id = await _get_ollama_provider_id(base_url, client)
        if not provider_id:
            logger.warning("Cannot find Ollama provider in Perplexica")
            return None

        payload = {
            "chatModel": {"providerId": provider_id, "key": chat_model},
            "embeddingModel": {"providerId": provider_id, "key": embedding_model},
            "optimizationMode": "speed",
            "sources": ["web"],
            "query": query,
            "history": [],
        }

        resp = await client.post(f"{base_url}/api/search", json=payload)
        resp.raise_for_status()
        data = resp.json()
        answer = data.get("message", "")
        sources = [
            {"title": s.get("metadata", {}).get("title", ""),
             "url":   s.get("metadata", {}).get("url", "")}
            for s in data.get("sources", [])
            if s.get("metadata", {}).get("url")
        ]
        logger.info("Perplexica search OK: %d chars, %d sources", len(answer), len(sources))
        return {"answer": answer[:2000], "sources": sources[:5], "query": query}


async def search(
    query: str,
    base_url: str = "http://localhost:3001",
    chat_model: str = "gemma4:e4b",
    embedding_model: str = "nomic-embed-text:latest",
    timeout: float = 300.0,
) -> dict | None:
    try:
        return await asyncio.wait_for(
            _do_search(query, base_url, chat_model, embedding_model, timeout),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning("Perplexica timeout after %.0fs for query: %s", timeout, query[:80])
    except httpx.ConnectError:
        logger.debug("Perplexica not reachable at %s", base_url)
    except Exception as exc:
        logger.warning("Perplexica error: %s — %r", type(exc).__name__, str(exc)[:200])
    return None
