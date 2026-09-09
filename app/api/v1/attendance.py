from fastapi import APIRouter

router = APIRouter(prefix="/attendance", tags=["Employee Attendance"])


@router.get("/health", summary="Module health check")
async def health_check():
    return {"module": "attendance", "status": "active"}
