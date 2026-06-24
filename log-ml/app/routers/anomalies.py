from fastapi import APIRouter
from app.models.schemas import AnomalyRequest, AnomalyResponse
from app.services.forest import score_windows, MIN_WINDOWS

router = APIRouter()


@router.post("/anomalies", response_model=AnomalyResponse)
async def detect_anomalies(req: AnomalyRequest) -> AnomalyResponse:
    results, model_trained, n_train = score_windows(
        req.tenant_id, req.host, req.windows
    )
    return AnomalyResponse(
        host=req.host,
        tenant_id=req.tenant_id,
        model_trained=model_trained,
        n_train_samples=n_train,
        min_windows_for_if=MIN_WINDOWS,
        results=results,
    )
