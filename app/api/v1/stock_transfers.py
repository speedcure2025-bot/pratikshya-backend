from fastapi import APIRouter

router = APIRouter(prefix="/stock-transfers", tags=["Stock Transfers"])


@router.get("/health", summary="Module health check")
async def health_check():
    return {"module": "stock_transfers", "status": "active"}
