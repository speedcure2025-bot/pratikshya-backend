from fastapi import APIRouter

router = APIRouter(prefix="/warehouses", tags=["Warehouses"])


@router.get("/health", summary="Module health check")
async def health_check():
    return {"module": "warehouses", "status": "active"}
