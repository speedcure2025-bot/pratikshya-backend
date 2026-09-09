from fastapi import APIRouter

router = APIRouter(prefix="/media-reviews", tags=["Media Asset Review"])


@router.get("/health", summary="Module health check")
async def health_check():
    return {"module": "media_reviews", "status": "active"}
