import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import config
from app.routers import analyze, health, ingest
from app.services.baseline_store import init_db


def _setup_logging() -> None:
    level = getattr(logging, config.logger.level.upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_logging()
    init_db()
    logging.getLogger(__name__).info(
        "log-analyzer starting — ollama=%s model=%s aiops_ml=%s enabled=%s",
        config.ollama.base_url,
        config.ollama.model,
        config.aiops_ml.base_url,
        config.aiops_ml.enabled,
    )
    yield


app = FastAPI(title="log-analyzer", version="1.0.0", lifespan=lifespan)

app.include_router(health.router)
app.include_router(analyze.router)
app.include_router(ingest.router)

_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")

@app.get("/")
async def dashboard():
    return FileResponse(os.path.join(_static_dir, "index.html"))


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logging.getLogger(__name__).exception("Unhandled error: %s", exc)
    return JSONResponse(status_code=500, content={"error": str(exc)})
