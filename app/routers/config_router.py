"""
GET /api/config  — return current config
POST /api/config — update config.yaml และ reload
"""

import logging
import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


class ConfigUpdate(BaseModel):
    godeye_callback_url: str | None = None
    godeye_enabled: bool = True
    log_ml_enabled: bool = True
    log_ml_base_url: str = "http://localhost:3050"
    perplexica_enabled: bool = False
    perplexica_base_url: str = "http://localhost:3001"
    perplexica_chat_model: str = "gemma4:e4b"
    perplexica_embedding_model: str = "nomic-embed-text:latest"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "gemma4:e4b"


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
            "chat_model": config.perplexica.chat_model,
            "embedding_model": config.perplexica.embedding_model,
        },
        "ollama": {
            "base_url": config.ollama.base_url,
            "model": config.ollama.model,
            "timeout": config.ollama.timeout,
            "temperature": config.ollama.temperature,
        },
        "aiops_ml": {
            "base_url": config.aiops_ml.base_url,
            "enabled": config.aiops_ml.enabled,
        },
    }


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
        data["perplexica"]["chat_model"] = body.perplexica_chat_model
        data["perplexica"]["embedding_model"] = body.perplexica_embedding_model

        data.setdefault("ollama", {})
        data["ollama"]["base_url"] = body.ollama_base_url
        data["ollama"]["model"] = body.ollama_model

        with open("config.yaml", "w") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

        # reload config in-process
        import app.config as cfg_module
        cfg_module.config = cfg_module.load_config()

        logger.info("Config updated via UI")
        return {"status": "ok", "message": "Config saved. Restart not required."}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
