from fastapi import APIRouter

router = APIRouter(prefix="/attributes", tags=["Variant Attributes"])


@router.get("/health", summary="Module health check")
async def health_check():
    return {"module": "attributes", "status": "active"}
