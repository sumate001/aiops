import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from app.config import config
from app.routers import analyze, health, ingest
from app.routers import config_router, results_router
from app.services.baseline_store import init_db
from app.services.result_store import init_result_table


def _setup_logging() -> None:
    level = getattr(logging, config.logger.level.upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_logging()
    init_db()
    init_result_table()
    logging.getLogger(__name__).info(
        "log-analyzer starting — ollama=%s model=%s aiops_ml=%s enabled=%s",
        config.ollama.base_url,
        config.ollama.model,
        config.aiops_ml.base_url,
        config.aiops_ml.enabled,
    )
    yield


app = FastAPI(title="log-analyzer", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(analyze.router)
app.include_router(ingest.router)
app.include_router(config_router.router)
app.include_router(results_router.router)


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics():
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)



@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logging.getLogger(__name__).exception("Unhandled error: %s", exc)
    return JSONResponse(status_code=500, content={"error": str(exc)})
