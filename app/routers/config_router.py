"""
GET /api/config  — return current config
POST /api/config — update config.yaml และ reload
"""

import logging
import os

import httpx
import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


class StageUpdate(BaseModel):
    override: bool = False
    provider: str = "ollama"
    base_url: str = "http://localhost:11434"
    model: str = "gemma4:e4b"
    api_key: str | None = None  # empty/None ⇒ keep stored key


class LLMUpdate(BaseModel):
    enabled: bool = False
    provider: str = "ollama"
    base_url: str = "http://localhost:11434"
    model: str = "gemma4:e4b"
    api_key: str | None = None  # empty/None ⇒ keep stored key
    mirofish: StageUpdate = StageUpdate()
    synthesizer: StageUpdate = StageUpdate()
    perplexica: StageUpdate = StageUpdate()


class ConfigUpdate(BaseModel):
    godeye_callback_url: str | None = None
    godeye_enabled: bool = True
    log_ml_enabled: bool = True
    log_ml_base_url: str = "http://localhost:3050"
    perplexica_enabled: bool = False
    perplexica_base_url: str = "http://localhost:3001"
    # A2 chat model lives under llm.perplexica (stage override), not here.
    perplexica_embedding_model: str = "Xenova/all-MiniLM-L6-v2"
    llm: LLMUpdate = LLMUpdate()


class FeedbackSubmit(BaseModel):
    host: str
    tenant_id: str = "internal"
    window_from: str
    actual_outcome: str  # "incident" | "no_incident"
    incident_type: str | None = None
    predicted_risk_level: str = "unknown"


@router.post("/feedback")
async def submit_feedback(body: FeedbackSubmit) -> dict:
    """Ground-truth feedback hook for predictor backtesting.

    Manual (operator) or future incident-management webhook calls this to record
    whether a prediction window actually turned into an incident, feeding
    godeyes_prediction_outcome_total for Grafana precision panels.
    """
    if body.actual_outcome not in ("incident", "no_incident"):
        raise HTTPException(status_code=400, detail={"error": "actual_outcome must be 'incident' or 'no_incident'"})

    from app.services.metrics import record_prediction_outcome
    record_prediction_outcome(
        host=body.host,
        tenant_id=body.tenant_id,
        predicted_risk_level=body.predicted_risk_level,
        actual_outcome=body.actual_outcome,
    )
    logger.info(
        "Feedback recorded — host=%s window_from=%s outcome=%s incident_type=%s",
        body.host, body.window_from, body.actual_outcome, body.incident_type,
    )
    return {"status": "ok"}


@router.get("/config")
async def get_config() -> dict:
    from app.config import config
    return {
        "godeye": {
            "callback_url": config.godeye.callback_url,
            "enabled": config.godeye.enabled,
        },
        "log_ml": {
            "base_url": config.log_ml.base_url,
            "enabled": config.log_ml.enabled,
        },
        "perplexica": {
            "base_url": config.perplexica.base_url,
            "enabled": config.perplexica.enabled,
            "embedding_model": config.perplexica.embedding_model,
        },
        "aiops_ml": {
            "base_url": config.aiops_ml.base_url,
            "enabled": config.aiops_ml.enabled,
        },
        "llm": {
            "enabled": config.llm.enabled,
            "provider": config.llm.provider,
            "base_url": config.llm.base_url,
            "model": config.llm.model,
            # never echo the secret back to the browser; just whether one is set
            "has_api_key": bool(config.llm.api_key),
            "mirofish": _stage_dict(config.llm.mirofish),
            "synthesizer": _stage_dict(config.llm.synthesizer),
            "perplexica": _stage_dict(config.llm.perplexica),
        },
    }


def _stage_dict(s) -> dict:
    return {
        "override": s.override,
        "provider": s.provider,
        "base_url": s.base_url,
        "model": s.model,
        "has_api_key": bool(s.api_key),
    }


@router.get("/llm/providers")
async def list_llm_providers() -> dict:
    """Registry of supported free / OpenAI-compatible LLM providers (for the UI)."""
    from app.services.llm_providers import PROVIDERS
    return {"providers": [p.model_dump() for p in PROVIDERS]}


@router.get("/llm/models")
async def list_llm_models(
    provider: str, base_url: str, api_key: str | None = None
) -> dict:
    """List models for any provider via its OpenAI-compatible /models route
    (or Ollama /api/tags). Lets the settings page populate the model dropdown."""
    from app.services import llm
    try:
        models = await llm.list_models(provider, base_url, api_key)
        return {"models": models}
    except Exception as exc:
        logger.warning("Failed to list models for %s @ %s: %s", provider, base_url, exc)
        return {"models": [], "error": str(exc)}


@router.get("/ollama/models")
async def list_ollama_models(base_url: str) -> dict:
    """Fetch installed model names from an arbitrary Ollama endpoint.

    The frontend can't reach private/Tailscale Ollama hosts directly (CORS +
    network), and the static /ollama-proxy rewrite is pinned to one URL — so the
    settings page asks the backend to probe whatever URL the user typed.
    """
    url = base_url.rstrip("/") + "/api/tags"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(url)
            r.raise_for_status()
            data = r.json()
        models = [m["name"] for m in data.get("models", []) if m.get("name")]
        return {"models": models}
    except Exception as exc:
        logger.warning("Failed to list Ollama models from %s: %s", url, exc)
        return {"models": [], "error": str(exc)}


@router.post("/config")
async def update_config(body: ConfigUpdate) -> dict:
    try:
        # อ่าน config.yaml ปัจจุบัน
        try:
            with open("config.yaml") as f:
                data = yaml.safe_load(f) or {}
        except FileNotFoundError:
            data = {}

        # update ค่าที่ส่งมา
        data.setdefault("godeye", {})
        data["godeye"]["callback_url"] = body.godeye_callback_url
        data["godeye"]["enabled"] = body.godeye_enabled

        data.setdefault("log_ml", {})
        data["log_ml"]["enabled"] = body.log_ml_enabled
        data["log_ml"]["base_url"] = body.log_ml_base_url

        data.setdefault("perplexica", {})
        data["perplexica"]["enabled"] = body.perplexica_enabled
        data["perplexica"]["base_url"] = body.perplexica_base_url
        data["perplexica"]["embedding_model"] = body.perplexica_embedding_model

        llm = data.setdefault("llm", {})
        llm["enabled"] = body.llm.enabled
        llm["provider"] = body.llm.provider
        llm["base_url"] = body.llm.base_url
        llm["model"] = body.llm.model
        # Only overwrite a key when the UI actually sends one — an empty/None
        # field means "keep the stored key" so re-saving doesn't wipe it.
        if body.llm.api_key:
            llm["api_key"] = body.llm.api_key
        for stage in ("mirofish", "synthesizer", "perplexica"):
            su = getattr(body.llm, stage)
            block = llm.setdefault(stage, {})
            block["override"] = su.override
            block["provider"] = su.provider
            block["base_url"] = su.base_url
            block["model"] = su.model
            if su.api_key:
                block["api_key"] = su.api_key

        with open("config.yaml", "w") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

        # Sync the env vars that _apply_env_overrides honours, otherwise a stale
        # OLLAMA_BASE_URL/etc in the process env would clobber what we just saved
        # on the next load_config() — making the UI look like "save didn't stick".
        os.environ["LOG_ML_BASE_URL"] = body.log_ml_base_url
        os.environ["LOG_ML_ENABLED"] = "true" if body.log_ml_enabled else "false"
        os.environ["PERPLEXICA_BASE_URL"] = body.perplexica_base_url
        os.environ["PERPLEXICA_ENABLED"] = "true" if body.perplexica_enabled else "false"
        os.environ["LLM_ENABLED"] = "true" if body.llm.enabled else "false"
        os.environ["LLM_PROVIDER"] = body.llm.provider
        os.environ["LLM_BASE_URL"] = body.llm.base_url
        os.environ["LLM_MODEL"] = body.llm.model
        if body.llm.api_key:
            os.environ["LLM_API_KEY"] = body.llm.api_key
        os.environ["CALLBACK_ENABLED"] = "true" if body.godeye_enabled else "false"
        if body.godeye_callback_url:
            os.environ["CALLBACK_URL"] = body.godeye_callback_url
        else:
            os.environ.pop("CALLBACK_URL", None)

        # reload config in-process
        import app.config as cfg_module
        cfg_module.config = cfg_module.load_config()

        logger.info("Config updated via UI")
        return {"status": "ok", "message": "Config saved. Restart not required."}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
