"""
LEAVE — API router (workforce domain).

  GET   /employee/leave                 own requests            (leave.view)
  POST  /employee/leave                 apply                   (leave.create)
  POST  /employee/leave/{id}/cancel     cancel own pending      (self; reviewers may cancel approved)
  GET   /admin/leave                    approve queue           (account manager + leave.view)
  POST  /admin/leave/{id}/decision      APPROVED | REJECTED     (leave.approve / leave.reject / leave.manage)

Rules (transitions, overlap, rejection reason, self-review ban) live in
`workforce_service` / `workforce_rules`; authority is the shared capability
surface. Approving materialises LEAVE attendance rows; rejecting or cancelling
clears only derived rows — never a punched day.
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
from app.models.auth.user import UserModel
from app.schemas.employee.workforce import (
    LeaveCreateRequest,
    LeaveDecisionRequest,
    LeaveDto,
    LeaveListResponse,
)
from app.services.employee.workforce_service import WorkforceService

router = APIRouter(tags=["Employee Leave"])


@router.get(
    "/employee/leave",
    response_model=LeaveListResponse,
    summary="Employee — own leave requests",
)
async def my_leave(
    status: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    me: UserModel = Depends(get_current_employee),
):
    service = WorkforceService(db)
    items, total = await service.list_leave(employee_user=me, status=status, page_size=100)
    return LeaveListResponse(items=items, total=total)


@router.post(
    "/employee/leave",
    response_model=LeaveDto,
    summary="Employee — apply for leave",
)
async def request_leave(
    req: LeaveCreateRequest,
    db: AsyncSession = Depends(get_db),
    me: UserModel = Depends(get_current_employee),
):
    await require_staff_permission(me, db, "leave.create")
    service = WorkforceService(db)
    return await service.create_leave(me, req)


@router.post(
    "/employee/leave/{leave_id}/cancel",
    response_model=LeaveDto,
    summary="Employee — cancel a leave request",
    description="Own PENDING request; a leave reviewer may additionally withdraw an APPROVED one.",
)
async def cancel_leave(
    leave_id: str,
    db: AsyncSession = Depends(get_db),
    me: UserModel = Depends(get_current_employee),
):
    service = WorkforceService(db)
    return await service.cancel_leave(me, leave_id)


@router.get(
    "/admin/leave",
    response_model=LeaveListResponse,
    summary="Supervisor/Admin — leave queue",
)
async def admin_list_leave(
    employee_id: Optional[str] = Query(default=None, description="PF code or user id"),
    status: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    actor: UserModel = Depends(get_current_account_manager),
):
    await require_staff_permission(actor, db, "leave.view")
    service = WorkforceService(db)
    items, total = await service.list_leave(employee_id=employee_id, status=status, page=page, page_size=page_size)
    return LeaveListResponse(items=items, total=total)


@router.post(
    "/admin/leave/{leave_id}/decision",
    response_model=LeaveDto,
    summary="Supervisor/Admin — approve or reject a leave request",
)
async def admin_decide_leave(
    leave_id: str,
    req: LeaveDecisionRequest,
    db: AsyncSession = Depends(get_db),
    actor: UserModel = Depends(get_current_account_manager),
):
    service = WorkforceService(db)
    return await service.decide_leave(actor, leave_id, req)
