import logging

import httpx
from fastapi import APIRouter

from app.config import config, OLLAMA_TIMEOUT

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/healthz")
async def healthz() -> dict:
    ollama_status = "unreachable"
    aiops_ml_status = "unreachable"

    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.get(f"{config.ollama.base_url}/api/tags")
            if resp.status_code == 200:
                ollama_status = "reachable"
        except Exception as exc:
            logger.debug("Ollama health check failed: %s", exc)

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
        "ollama": ollama_status,
        "ollama_model": config.ollama.model,
        "aiops_ml": aiops_ml_status,
    }
