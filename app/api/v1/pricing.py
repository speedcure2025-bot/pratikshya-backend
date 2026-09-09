from fastapi import APIRouter

router = APIRouter(prefix="/pricing", tags=["Pricing & Tax"])


@router.get("/health", summary="Module health check")
async def health_check():
    return {"module": "pricing", "status": "active"}
