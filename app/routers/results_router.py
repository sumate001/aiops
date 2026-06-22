"""
GET /api/results         — list recent analysis results
GET /api/results/{id}    — full payload of a single result
GET /api/status          — pipeline component health check
"""

import logging
import httpx
from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


@router.get("/results")
async def list_results(
    limit: int = Query(50, ge=1, le=200),
    tenant_id: str | None = None,
) -> dict:
    from app.services.result_store import get_results
    rows = get_results(limit=limit, tenant_id=tenant_id)
    return {"results": rows, "total": len(rows)}


@router.get("/results/{result_id}")
async def get_result(result_id: int) -> dict:
    from app.services.result_store import get_result_by_id
    data = get_result_by_id(result_id)
    if data is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Result not found")
    return data


@router.get("/status")
async def pipeline_status() -> dict:
    from app.config import config

    async def _check(url: str, path: str = "/healthz") -> dict:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                r = await client.get(f"{url}{path}")
                return {"status": "up", "code": r.status_code}
        except Exception as exc:
            return {"status": "down", "error": str(exc)[:80]}

    log_ml = await _check(config.log_ml.base_url) if config.log_ml.enabled else {"status": "disabled"}
    perplexica = await _check(config.perplexica.base_url, "/") if config.perplexica.enabled else {"status": "disabled"}
    ollama = await _check(config.ollama.base_url, "/api/tags") if True else {"status": "disabled"}

    return {
        "agents": {
            "A1_rule":        {"status": "up", "note": "always on"},
            "A1_isolation_forest": {**log_ml, "enabled": config.log_ml.enabled},
            "A2_perplexica":  {**perplexica, "enabled": config.perplexica.enabled},
            "A3_mirofish":    {"status": "up", "note": "always on"},
            "AA_synthesizer": {"status": "up", "note": "always on"},
        },
        "integrations": {
            "ollama":   {**ollama, "model": config.ollama.model},
            "godeye_callback": {
                "url": config.godeye.callback_url,
                "enabled": config.godeye.enabled,
            },
        },
    }
