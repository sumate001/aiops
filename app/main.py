import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from app.config import config
from app.routers import analyze, health, ingest
from app.routers import config_router, results_router
from app.services import perplexica_client
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
        "log-analyzer starting — llm=%s/%s model=%s aiops_ml=%s enabled=%s",
        config.llm.provider,
        config.llm.base_url,
        config.llm.model,
        config.aiops_ml.base_url,
        config.aiops_ml.enabled,
    )
    # Prime A2 (Perplexica) in the background so the first ingest doesn't eat the
    # embedding cold-start. Non-blocking: readiness/health come up immediately.
    if config.perplexica.enabled:
        asyncio.create_task(perplexica_client.warm_up())
    yield


app = FastAPI(title="log-analyzer", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# API responses are live state — never let a proxy/browser cache or revalidate
# them. Without this, a conditional GET can come back 304 with an empty body;
# the dashboard's fetch().json() then throws and every status renders "down".
@app.middleware("http")
async def no_store_api(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(("/api", "/healthz")):
        response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response

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
