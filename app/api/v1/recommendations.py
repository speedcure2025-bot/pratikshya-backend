"""Customer behavioral data; no operational audit/history read API."""
from typing import Literal
from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.exc import SQLAlchemyError
from app.core.exceptions import AppException
from app.core.middleware import limiter
from app.dependencies import get_current_customer, get_db
from app.schemas.catalog.product import OkResponse
from app.schemas.catalog.product import RecommendationsResponse
from app.schemas.customer.recommendation import ProductInteractionRequest
from app.services.catalog.recommendation_service import RecommendationService

router = APIRouter(prefix="/customers/me", tags=["Recommendations"])


def unavailable():
    return AppException(message="Recommendations are temporarily unavailable.", status_code=503, error_code="RECOMMENDATIONS_UNAVAILABLE")


@router.post("/product-interactions", response_model=OkResponse,
             summary="Record a customer product VIEW or CLICK",
             description="Customer JWT required. Server-owned identity/time; optional UUID idempotencyKey. Repeated events are deduplicated. Business events cannot be submitted by clients.")
@limiter.limit("60/minute")
async def track_interaction(request: Request, response: Response, body: ProductInteractionRequest,
                            user=Depends(get_current_customer), db=Depends(get_db)):
    response.headers["Cache-Control"] = "private, no-store"
    try:
        await RecommendationService(db).record(user.id, body.product_id, body.event_type, body.idempotency_key)
        # Commit inside the guarded boundary, including connection/commit failures.
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        raise unavailable()
    return OkResponse()


@router.get("/recommendations", response_model=RecommendationsResponse,
            summary="Current customer's recommendations",
            description="type=personalized requires sufficient real history; because-viewed uses the latest visible durable VIEW. Empty items means no meaningful results. No scores or private history returned. Never shared-cached.")
@limiter.limit("60/minute")
async def personal_recommendations(request: Request, response: Response,
                                   type: Literal["personalized", "because-viewed"] = "personalized",
                                   limit: int = Query(4, ge=1, le=12),
                                   user=Depends(get_current_customer), db=Depends(get_db)):
    response.headers["Cache-Control"] = "private, no-store"
    try:
        items = await RecommendationService(db).personal(user.id, type, limit)
    except SQLAlchemyError:
        await db.rollback()
        raise unavailable()
    return RecommendationsResponse(items=items)
