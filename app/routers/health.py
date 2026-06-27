import logging

import httpx
from fastapi import APIRouter

from app.config import config, OLLAMA_TIMEOUT

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/healthz")
async def healthz() -> dict:
    llm_status = "unreachable"
    aiops_ml_status = "unreachable"

    async with httpx.AsyncClient(timeout=5.0) as client:
        if config.llm.provider == "ollama":
            try:
                resp = await client.get(f"{config.llm.base_url}/api/tags")
                if resp.status_code == 200:
                    llm_status = "reachable"
            except Exception as exc:
                logger.debug("Ollama health check failed: %s", exc)
        else:
            # remote OpenAI-compatible providers: no cheap unauthenticated probe
            llm_status = "configured"

        if config.aiops_ml.enabled:
            try:
                resp = await client.get(f"{config.aiops_ml.base_url}/healthz")
                if resp.status_code == 200:
                    aiops_ml_status = "reachable"
            except Exception as exc:
                logger.debug("aiops-ml health check failed: %s", exc)
        else:
            aiops_ml_status = "disabled"

    return {
        "status": "ok",
        "llm": llm_status,
        "llm_provider": config.llm.provider,
        "llm_model": config.llm.model,
        "aiops_ml": aiops_ml_status,
    }
