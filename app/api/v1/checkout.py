from fastapi import APIRouter

router = APIRouter(prefix="/checkout", tags=["Checkout Flow"])


@router.get("/health", summary="Module health check")
async def health_check():
    return {"module": "checkout", "status": "active"}
