from fastapi import APIRouter

router = APIRouter(prefix="/performance", tags=["Employee Performance"])


@router.get("/health", summary="Module health check")
async def health_check():
    return {"module": "performance", "status": "active"}
