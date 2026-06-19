from fastapi import APIRouter
from app.models.schemas import ModelsResponse
from app.services.forest import list_models, MODEL_STORE

router = APIRouter()


@router.get("/models", response_model=ModelsResponse)
async def get_models() -> ModelsResponse:
    return ModelsResponse(models=list_models(), model_store=MODEL_STORE)
