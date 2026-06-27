"""
A2 — Perplexica client for external knowledge enrichment
ดึง Ollama provider UUID จาก /api/providers แล้วใช้ใน /api/search
"""

import asyncio
import logging
import httpx

logger = logging.getLogger(__name__)

_provider_cache: dict[str, str] = {}   # base_url → ollama provider UUID
# (perplexica base, chat provider id, chat base_url) → created chat provider UUID
_chat_provider_cache: dict[tuple, str] = {}


async def _list_providers(base_url: str, client: httpx.AsyncClient) -> list[dict]:
    """Authoritative provider list (includes `type` + `config`). The runtime
    /api/providers route drops the `type` field and hides providers that can't
    currently connect, so use /api/config's modelProviders instead."""
    r = await client.get(f"{base_url}/api/config")
    r.raise_for_status()
    body = r.json()
    return (body.get("values") or body).get("modelProviders", [])


async def _get_ollama_provider_id(base_url: str, client: httpx.AsyncClient) -> str | None:
    cached = _provider_cache.get(base_url)
    if cached:
        return cached
    try:
        for p in await _list_providers(base_url, client):
            if p.get("type") == "ollama" or "ollama" in p.get("name", "").lower():
                _provider_cache[base_url] = p["id"]
                return p["id"]
    except Exception as exc:
        logger.debug("Failed to fetch Perplexica providers: %s", exc)
    return None


async def _get_embedding(
    base_url: str, client: httpx.AsyncClient, preferred: str | None = None
) -> tuple[str, str] | None:
    """Resolve an embedding (providerId, modelKey). Prefers the local
    Transformers provider (ships with Perplexica, needs no Ollama); falls back
    to any provider that exposes embedding models."""
    try:
        providers = await _list_providers(base_url, client)
    except Exception as exc:
        logger.debug("Failed to list providers for embeddings: %s", exc)
        return None

    def pick(p: dict) -> tuple[str, str] | None:
        models = p.get("embeddingModels") or []
        if not models:
            return None
        if preferred:
            for m in models:
                if m.get("key") == preferred or m.get("name") == preferred:
                    return p["id"], m["key"]
        return p["id"], models[0]["key"]

    # transformers first, then anything else with embeddings
    for want_transformers in (True, False):
        for p in providers:
            is_tf = p.get("type") == "transformers" or "transformers" in (p.get("name", "").lower())
            if want_transformers != is_tf:
                continue
            res = pick(p)
            if res:
                return res
    return None


async def _ensure_chat_provider(
    perp_base: str,
    client: httpx.AsyncClient,
    provider: str,
    chat_base_url: str,
    api_key: str | None,
) -> str | None:
    """Resolve (creating if needed) the Perplexica provider that backs the chat
    model. `ollama` reuses the existing Ollama provider. Providers Perplexica
    supports natively (groq, gemini) are created with that native type so their
    model list resolves correctly; everything else uses the generic `openai`
    type pointed at its base URL + key."""
    if provider == "ollama":
        return await _get_ollama_provider_id(perp_base, client)

    cache_key = (perp_base, provider, chat_base_url)
    if cache_key in _chat_provider_cache:
        return _chat_provider_cache[cache_key]

    # provider id → (Perplexica native type, config). Generic OpenAI-compatible
    # providers fall back to the "openai" type with an explicit base URL.
    NATIVE = {"groq", "gemini", "anthropic", "openai"}
    if provider in NATIVE:
        ptype = provider
        cfg_create = {"apiKey": api_key or ""}
    else:
        ptype = "openai"
        cfg_create = {"baseURL": chat_base_url, "apiKey": api_key or ""}

    name = f"aiops-{provider}"
    try:
        # reuse an existing matching provider if one is already registered
        for p in await _list_providers(perp_base, client):
            cfg = p.get("config") or {}
            if p.get("name") == name or (
                p.get("type") == ptype and ptype == "openai" and cfg.get("baseURL") == chat_base_url
            ):
                _chat_provider_cache[cache_key] = p["id"]
                return p["id"]
        # otherwise create it
        r = await client.post(
            f"{perp_base}/api/providers",
            json={"type": ptype, "name": name, "config": cfg_create},
        )
        r.raise_for_status()
        pid = r.json()["provider"]["id"]
        _chat_provider_cache[cache_key] = pid
        return pid
    except Exception as exc:
        logger.warning("Failed to ensure Perplexica chat provider %s: %s", provider, exc)
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
    chat_provider: str = "ollama",
    chat_base_url: str = "http://localhost:11434",
    chat_api_key: str | None = None,
) -> dict | None:
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10, read=timeout, write=30)) as client:
        chat_provider_id = await _ensure_chat_provider(
            base_url, client, chat_provider, chat_base_url, chat_api_key
        )
        if not chat_provider_id:
            logger.warning("Cannot resolve Perplexica chat provider '%s'", chat_provider)
            return None
        # Embeddings run on the local Transformers provider (no Ollama needed);
        # chat providers like Groq don't serve embeddings.
        embed = await _get_embedding(base_url, client, embedding_model)
        if not embed:
            logger.warning("No embedding model available in Perplexica")
            return None
        embed_provider_id, embed_key = embed

        payload = {
            "chatModel": {"providerId": chat_provider_id, "key": chat_model},
            "embeddingModel": {"providerId": embed_provider_id, "key": embed_key},
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
    chat_provider: str = "ollama",
    chat_base_url: str = "http://localhost:11434",
    chat_api_key: str | None = None,
) -> dict | None:
    try:
        return await asyncio.wait_for(
            _do_search(query, base_url, chat_model, embedding_model, timeout,
                       chat_provider, chat_base_url, chat_api_key),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning("Perplexica timeout after %.0fs for query: %s", timeout, query[:80])
    except httpx.ConnectError:
        logger.debug("Perplexica not reachable at %s", base_url)
    except Exception as exc:
        logger.warning("Perplexica error: %s — %r", type(exc).__name__, str(exc)[:200])
    return None
