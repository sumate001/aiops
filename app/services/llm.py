"""Unified LLM gateway.

Single `generate()` entry point used by every LLM caller in the app
(A1 analyze, MiroFish, AA Synthesizer). It dispatches to:

  - the native Ollama client (`ollama.generate`) when provider == "ollama"
  - an OpenAI-compatible chat-completions call for every other provider in
    `llm_providers.PROVIDERS`

Callers keep the original keyword signature
`generate(prompt, model, base_url, timeout, temperature)` so existing call
sites and the `ollama_generate=` callables in synthesizer/mirofish keep working
unchanged. The active provider/key default to the app config but can be
overridden per call.
"""

import logging
import os

import httpx

from app.services import ollama
from app.services.llm_providers import get_provider

logger = logging.getLogger(__name__)


class LLMError(Exception):
    pass


def _resolve_api_key(provider_id: str, explicit: str | None) -> str | None:
    """Config value wins; otherwise fall back to the provider's env var."""
    if explicit:
        return explicit
    info = get_provider(provider_id)
    if info and info.api_key_env:
        return os.environ.get(info.api_key_env)
    return None


async def _openai_chat(
    *,
    prompt: str,
    model: str,
    base_url: str,
    api_key: str | None,
    timeout: float,
    temperature: float,
) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            body = resp.json()
            return body["choices"][0]["message"]["content"] or ""
        except httpx.ConnectError as exc:
            raise LLMError(f"llm unreachable: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise LLMError(f"llm timeout: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:300]
            raise LLMError(
                f"llm error {exc.response.status_code}: {detail}"
            ) from exc
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"llm malformed response: {exc}") from exc


async def generate(
    prompt: str,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 120.0,
    temperature: float = 0.1,
    *,
    provider: str | None = None,
    api_key: str | None = None,
) -> str:
    """Generate a completion via the configured (or specified) provider.

    All of provider/model/base_url/api_key fall back to `config.llm` when not
    passed, so most callers only need to supply `prompt`.
    """
    from app.config import config

    provider = provider or config.llm.provider
    model = model or config.llm.model
    base_url = base_url or config.llm.base_url

    if provider == "ollama":
        return await ollama.generate(
            prompt=prompt,
            model=model,
            base_url=base_url,
            timeout=timeout,
            temperature=temperature,
        )

    info = get_provider(provider)
    if info is None:
        raise LLMError(f"unknown LLM provider: {provider!r}")

    return await _openai_chat(
        prompt=prompt,
        model=model,
        base_url=base_url,
        api_key=_resolve_api_key(provider, api_key or config.llm.api_key),
        timeout=timeout,
        temperature=temperature,
    )


async def list_models(
    provider: str, base_url: str, api_key: str | None = None
) -> list[str]:
    """List available models for a provider (for the settings UI)."""
    if provider == "ollama":
        url = base_url.rstrip("/") + "/api/tags"
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(url)
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", []) if m.get("name")]

    key = _resolve_api_key(provider, api_key)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    url = base_url.rstrip("/") + "/models"
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(url, headers=headers)
        r.raise_for_status()
        data = r.json().get("data", [])
        return [m["id"] for m in data if m.get("id")]
