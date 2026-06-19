import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers.anomalies import router as anomalies_router
from app.routers.train import router as train_router
from app.routers.models_router import router as models_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "log-ml starting — store=%s min_windows=%s contamination=%s",
        os.environ.get("ML_MODEL_STORE", "model_store"),
        os.environ.get("ML_MIN_WINDOWS", "30"),
        os.environ.get("ML_CONTAMINATION", "0.05"),
    )
    yield


app = FastAPI(
    title="log-ml",
    version="1.0.0",
    description="Isolation Forest anomaly detection for AIOps log windows",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(anomalies_router)
app.include_router(train_router)
app.include_router(models_router)


@app.get("/healthz")
async def healthz():
    from app.services.forest import list_models
    models = list_models()
    return {
        "status": "ok",
        "trained_models": len(models),
        "hosts": [m.host for m in models],
    }
