from fastapi import APIRouter
from app.models.schemas import TrainRequest, TrainResponse
from app.services.forest import train, MIN_WINDOWS

router = APIRouter()


@router.post("/train", response_model=TrainResponse)
async def train_model(req: TrainRequest) -> TrainResponse:
    status, n = train(req.tenant_id, req.host, req.windows)
    if status == "skipped":
        msg = f"Need ≥{MIN_WINDOWS} windows, got {n}. Using rule-based fallback."
    else:
        msg = f"Model {status} with {n} windows."
    return TrainResponse(
        host=req.host,
        tenant_id=req.tenant_id,
        status=status,
        n_samples=n,
        message=msg,
    )
