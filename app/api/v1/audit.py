"""
Audit — API router.

Read-only access to the shared activity diary (`audit_activity_log`).

  GET /audit/logs?action=&actor=&targetProductId=&targetEmployeeId=&page=&pageSize=
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_admin, get_db
from app.models.audit.activity_log import ActivityLogModel
from app.models.auth.user import UserModel

router = APIRouter(prefix="/audit", tags=["Audit"])


def _log_dto(entry: ActivityLogModel) -> dict:
    return {
        "id":                 entry.id,
        "at":                 entry.created_at.isoformat() if entry.created_at else None,
        "actorEmployeeId":    entry.actor_employee_id,
        "actorName":          entry.actor_name,
        "targetEmployeeId":   entry.target_employee_id,
        "targetProductId":    entry.target_product_id,
        "targetOfferId":      entry.target_offer_id,
        "targetCategoryId":   entry.target_category_id,
        "targetCollectionId": entry.target_collection_id,
        "targetOrderId":      entry.target_order_id,
        "targetReturnId":     entry.target_return_id,
        "targetMediaId":      entry.target_media_id,
        "action":             entry.action,
        "summary":            entry.summary,
    }


@router.get("/health", summary="Module health check")
async def health_check():
    return {"module": "audit", "status": "active"}


@router.get("/logs", summary="List activity log entries (admin)")
async def list_logs(
    action: Optional[str] = Query(default=None),
    actor: Optional[str] = Query(default=None, description="actor employee id or name"),
    target_product_id: Optional[str] = Query(default=None),
    target_employee_id: Optional[str] = Query(default=None),
    target_order_id: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None, description="free-text search"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _admin: UserModel = Depends(get_current_admin),
):
    stmt = select(ActivityLogModel)
    if action:
        stmt = stmt.where(ActivityLogModel.action == action)
    if target_product_id:
        stmt = stmt.where(ActivityLogModel.target_product_id == target_product_id)
    if target_employee_id:
        stmt = stmt.where(ActivityLogModel.target_employee_id == target_employee_id)
    if target_order_id:
        stmt = stmt.where(ActivityLogModel.target_order_id == target_order_id)
    if actor:
        term = f"%{actor}%"
        stmt = stmt.where(
            or_(ActivityLogModel.actor_employee_id.ilike(term), ActivityLogModel.actor_name.ilike(term))
        )
    if q:
        term = f"%{q}%"
        stmt = stmt.where(or_(ActivityLogModel.action.ilike(term), ActivityLogModel.summary.ilike(term)))

    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    stmt = stmt.order_by(ActivityLogModel.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()

    return {
        "items":    [_log_dto(r) for r in rows],
        "total":    total,
        "page":     page,
        "pageSize": page_size,
    }
