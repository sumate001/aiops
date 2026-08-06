"""
A2 — Perplexica client for external knowledge enrichment
ดึง Ollama provider UUID จาก /api/providers แล้วใช้ใน /api/search
"""

import asyncio
import logging
import re
import time

import httpx

logger = logging.getLogger(__name__)

# ── Search-result cache + upstream cooldown ─────────────────────────────────
# GodEye streams windows continuously, so the same host produces a nearly
# identical query every cycle. Without this, each ingest fans out one SearXNG
# search per host back-to-back and the upstream engines (DuckDuckGo, Brave)
# CAPTCHA/429-ban the instance.
_CACHE_TTL = 6 * 3600          # reuse an answer for the same query for 6h
_MIN_SEARCH_INTERVAL = 60.0    # at most one real upstream search per minute
_result_cache: dict[str, tuple[float, dict]] = {}   # query → (fetched_at, result)
_last_search_at: float = 0.0

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


# Grammar filler that survives normalisation but carries no search signal. Kept
# deliberately small: domain words ("failed", "timeout", "refused") must stay.
_STOPWORDS = frozenset("""
    a an the and or but if of in on at to for from by with is was are were be
    been being it its this that these those when while trying try get got has
    have had do does did not no
""".split())

# Placeholders normalize.py leaves behind (<IP>, <NUM>, <TS>, …). They mark
# where the varying parts were; as search terms they are worse than nothing.
_PLACEHOLDER = re.compile(r"<[A-Z]+>")


def _clean_error(msg: str) -> str:
    """Reduce a raw error line to a short, searchable keyword phrase.

    Delegates the hard part to normalize.py rather than stripping digits here.
    The old local regex dropped every number of three digits or more, which took
    `ORA-01555`, `HTTP 503` and `errno 111` with it — precisely the tokens most
    likely to find the right page. normalize.py protects those and blanks only
    the genuinely varying parts.
    """
    from app.services.normalize import normalize_message  # local: avoid cycle

    text = _PLACEHOLDER.sub(" ", normalize_message(msg)).lower()
    # Keep alphanumerics plus the punctuation that lives inside error codes.
    text = re.sub(r"[^a-z0-9 _.\-]+", " ", text)
    words = [w.strip("-._") for w in text.split()]
    words = [w for w in words if len(w) > 1 and w not in _STOPWORDS]
    return " ".join(words[:6])


# The surviving SearXNG engines match near-literally and intersect their terms,
# so every extra concept shrinks the result set instead of refining it.
# Measured against this instance on 2026-08-05:
#   "deadlock"                          → 24 results
#   "slow query"                        → 13
#   "slow query deadlock troubleshooting" → 0     ← both good phrases, combined
# The old builder concatenated frame + 3 keywords + an 8-word error phrase into
# ~13-word queries that returned nothing — and on an empty result set Perplexica
# still answers, from the model's own memory, with no sources at all.
# So: pick the single most specific signal, keep it to three words, and stop.
_MAX_QUERY_WORDS = 3


def build_query(
    top_frame: str | None,
    top_keywords: list[str],
    top_error_msgs: list[str],
    host: str,
    anomaly_metrics: list[str] | None = None,
) -> str:
    """Build a short, searchable web query from the single strongest signal.

    Returns "" when there is no searchable evidence — callers should skip A2.
    `top_frame` and `host` are deliberately unused: a bare frame ("hardware")
    or hostname does return results, but generic ones, which are worse than no
    research at all because they pass the judge's source gate while saying
    nothing about this incident.
    """
    def trim(text: str) -> str:
        return " ".join(text.lower().split()[:_MAX_QUERY_WORDS])

    from app.services.normalize import extract_error_codes  # local: avoid cycle

    # A specific error code beats everything else: "ORA-01555" identifies one
    # failure, while the prose around it ("snapshot too old on tablespace")
    # describes a whole family. It's also short, which matters — these engines
    # intersect terms, so fewer concepts means more results.
    for msg in (top_error_msgs or [])[:2]:
        codes = extract_error_codes(msg)
        if codes:
            return trim(codes[0])

    # Frame keywords are curated single concepts ("deadlock", "connection
    # refused") — the next best thing. The de-noised error phrase is the
    # fallback; anomalous metric names cover metrics-only windows.
    if top_keywords:
        return trim(top_keywords[0])
    if top_error_msgs:
        return trim(_clean_error(top_error_msgs[0]))
    if anomaly_metrics:
        return trim(anomaly_metrics[0].replace("_", " ") + " high")
    return ""


async def _do_search(
    query: str,
    base_url: str,
    chat_model: str,
    embedding_model: str,
    timeout: float,
    chat_provider: str = "ollama",
    chat_base_url: str = "http://localhost:11434",
    chat_api_key: str | None = None,
    mode: str = "speed",
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
            "optimizationMode": mode,
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
        return {"answer": answer[:4000], "sources": sources[:5], "query": query}


async def search(
    query: str,
    base_url: str = "http://localhost:3001",
    chat_model: str = "gemma4:e4b",
    embedding_model: str = "nomic-embed-text:latest",
    timeout: float = 300.0,
    chat_provider: str = "ollama",
    chat_base_url: str = "http://localhost:11434",
    chat_api_key: str | None = None,
    mode: str = "speed",
) -> dict | None:
    global _last_search_at

    cache_key = query.strip().lower()
    now = time.monotonic()
    cached = _result_cache.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL:
        logger.info("A2 cache hit (age %.0fs) for query: %s", now - cached[0], query[:80])
        return cached[1]

    # Cooldown: protect upstream engines from burst searches (one per host per
    # ingest cycle adds up fast). Prefer a stale cached answer over nothing.
    if now - _last_search_at < _MIN_SEARCH_INTERVAL:
        if cached:
            logger.info("A2 cooldown — serving stale cache for query: %s", query[:80])
            return cached[1]
        logger.info("A2 cooldown — skipping search (%.0fs since last) for query: %s",
                    now - _last_search_at, query[:80])
        return None

    try:
        _last_search_at = now
        result = await asyncio.wait_for(
            _do_search(query, base_url, chat_model, embedding_model, timeout,
                       chat_provider, chat_base_url, chat_api_key, mode),
            timeout=timeout,
        )
        if result and result.get("answer"):
            _result_cache[cache_key] = (time.monotonic(), result)
        return result
    except asyncio.TimeoutError:
        logger.warning("Perplexica timeout after %.0fs for query: %s", timeout, query[:80])
    except httpx.ConnectError:
        logger.debug("Perplexica not reachable at %s", base_url)
    except Exception as exc:
        logger.warning("Perplexica error: %s — %r", type(exc).__name__, str(exc)[:200])
    return None


async def warm_up() -> None:
    """Fire one throwaway A2 search at startup so the first real request isn't
    paying the cold-start cost: it loads Perplexica's local embedding model into
    RAM and primes the chat/embedding provider-id caches in this process.
    Fully best-effort — failures are logged and swallowed, never block boot."""
    from app.config import config, PERPLEXICA_TIMEOUT  # local import: avoid cycle

    if not config.perplexica.enabled:
        return
    px = config.llm.resolve("perplexica")
    logger.info("A2 warm-up — priming Perplexica embeddings + provider caches")
    result = await search(
        query="aiops warmup",
        base_url=config.perplexica.base_url,
        chat_model=px.model,
        embedding_model=config.perplexica.embedding_model,
        timeout=PERPLEXICA_TIMEOUT,
        chat_provider=px.provider,
        chat_base_url=px.base_url,
        chat_api_key=px.api_key,
        mode=config.perplexica.mode,
    )
    logger.info("A2 warm-up %s", "done" if result is not None else "skipped (Perplexica not ready)")
    # Don't let the warm-up count toward the upstream cooldown — the first real
    # analysis after boot should still be allowed to search.
    global _last_search_at
    _last_search_at = 0.0
