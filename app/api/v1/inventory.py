from fastapi import APIRouter

router = APIRouter(prefix="/inventory", tags=["Inventory Stock"])


@router.get("/health", summary="Module health check")
async def health_check():
    return {"module": "inventory", "status": "active"}
