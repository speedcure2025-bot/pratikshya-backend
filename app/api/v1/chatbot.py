from fastapi import APIRouter

router = APIRouter(prefix="/chatbot", tags=["AI RAG Chatbot"])


@router.get("/health", summary="Module health check")
async def health_check():
    return {"module": "chatbot", "status": "active"}
