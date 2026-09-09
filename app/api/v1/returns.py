from fastapi import APIRouter

router = APIRouter(prefix="/returns", tags=["Returns Management"])


@router.get("/health", summary="Module health check")
async def health_check():
    return {"module": "returns", "status": "active"}
