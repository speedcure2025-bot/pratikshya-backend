from fastapi import APIRouter

router = APIRouter(prefix="/variants", tags=["Product Variants"])


@router.get("/health", summary="Module health check")
async def health_check():
    return {"module": "variants", "status": "active"}
