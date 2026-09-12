"""
PERFORMANCE — API router (workforce domain).

The existing product shape: periodic review rows (rating 1–5,
MONTHLY|QUARTERLY|ANNUAL, reviewer, comments) on `employee_performance`.
No score formulas are invented server-side — the employee dashboard derives
target achievement from real operational data client-side, and this surface
stores exactly the review record the existing model defines.

  GET   /employee/performance?period=   own reviews + summary (self, no permission needed to SEE own)
  GET   /admin/performance              account-manager list, bounded (performance.view)
  POST  /admin/performance              record review   (performance.review / performance.manage)
  PATCH /admin/performance/{id}         update review   (same codes)
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import (
    get_current_account_manager,
    get_current_employee,
    get_db,
    require_staff_permission,
)
from app.core.exceptions import ForbiddenException
from app.models.auth.user import UserModel
from app.schemas.employee.workforce import (
    PerformanceCreateRequest,
    PerformanceDto,
    PerformanceListResponse,
    PerformanceSelfResponse,
    PerformanceUpdateRequest,
)
from app.services.employee.workforce_service import WorkforceService

router = APIRouter(tags=["Employee Performance"])


@router.get("/performance/health", summary="Module health check")
async def health_check():
    return {"module": "performance", "status": "active"}


@router.get(
    "/employee/performance",
    response_model=PerformanceSelfResponse,
    summary="Employee — own performance reviews",
)
async def my_performance(
    period: Optional[str] = Query(default=None, description="MONTHLY | QUARTERLY | ANNUAL"),
    db: AsyncSession = Depends(get_db),
    me: UserModel = Depends(get_current_employee),
):
    service = WorkforceService(db)
    items, _total = await service.list_performance(employee_user=me, period=period, page_size=100)
    from app.services.employee import workforce_rules as rules

    return PerformanceSelfResponse(reviews=items, summary=rules.performance_summary(items))


@router.get(
    "/admin/performance",
    response_model=PerformanceListResponse,
    summary="Supervisor/Admin — performance reviews",
)
async def admin_list_performance(
    employee_id: Optional[str] = Query(default=None, description="PF code or user id"),
    period: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    actor: UserModel = Depends(get_current_account_manager),
):
    await require_staff_permission(actor, db, "performance.view")
    service = WorkforceService(db)
    items, total = await service.list_performance(
        employee_id=employee_id, period=period, page=page, page_size=page_size
    )
    return PerformanceListResponse(items=items, total=total)


async def _require_reviewer(actor: UserModel, db: AsyncSession) -> None:
    """performance.review OR performance.manage — same surface, any-of."""
    last_error: Optional[Exception] = None
    for code in ("performance.review", "performance.manage"):
        try:
            await require_staff_permission(actor, db, code)
            return
        except ForbiddenException as exc:
            last_error = exc
    raise last_error  # type: ignore[misc]


@router.post(
    "/admin/performance",
    response_model=PerformanceDto,
    summary="Supervisor/Admin — record a performance review",
)
async def admin_create_performance(
    req: PerformanceCreateRequest,
    db: AsyncSession = Depends(get_db),
    actor: UserModel = Depends(get_current_account_manager),
):
    await _require_reviewer(actor, db)
    service = WorkforceService(db)
    return await service.create_performance(actor, req)


@router.patch(
    "/admin/performance/{performance_id}",
    response_model=PerformanceDto,
    summary="Supervisor/Admin — update a performance review",
)
async def admin_update_performance(
    performance_id: str,
    req: PerformanceUpdateRequest,
    db: AsyncSession = Depends(get_db),
    actor: UserModel = Depends(get_current_account_manager),
):
    await _require_reviewer(actor, db)
    service = WorkforceService(db)
    return await service.update_performance(actor, performance_id, req)
