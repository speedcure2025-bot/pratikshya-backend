"""
ATTENDANCE — API router (workforce domain).

Employee self-service (contract API-ATT-04..07):

  POST /employee/attendance/check-in    {at?}   → punch in (409 already in, 403 on approved leave)
  POST /employee/attendance/check-out   {at?}   → punch out (409 no check-in / already out)
  GET  /employee/attendance/today               → today's row for the signed-in employee
  GET  /employee/attendance?month=YYYY-MM       → own history + bounded summary

Supervisor/admin day view (account-manager scope; the Admin per-employee
surfaces live on /admin/employees/{id}/attendance in employees.py):

  GET  /admin/attendance/day?date=YYYY-MM-DD    → whole-house rows for one day

Authorization is the shared capability surface (`require_staff_permission`);
business legality is `workforce_rules`. No second engine, no second log.
"""

from datetime import date as date_t
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessLogicException
from app.dependencies import (
    get_current_account_manager,
    get_current_employee,
    get_db,
    require_staff_permission,
)
from app.models.auth.user import UserModel
from app.schemas.employee.workforce import (
    MyAttendanceResponse,
    PunchRequest,
    PunchResult,
    TodayAttendanceResponse,
    WorkforceAttendanceDto,
)
from app.services.employee import workforce_rules as rules
from app.services.employee.workforce_service import WorkforceService

router = APIRouter(tags=["Employee Attendance"])


@router.get("/attendance/health", summary="Module health check")
async def health_check():
    return {"module": "attendance", "status": "active"}


@router.post(
    "/employee/attendance/check-in",
    response_model=PunchResult,
    summary="Employee — punch in (self)",
)
async def employee_check_in(
    req: PunchRequest,
    db: AsyncSession = Depends(get_db),
    me: UserModel = Depends(get_current_employee),
):
    await require_staff_permission(me, db, "attendance.checkin")
    service = WorkforceService(db)
    return await service.punch_in(me, req.at)


@router.post(
    "/employee/attendance/check-out",
    response_model=PunchResult,
    summary="Employee — punch out (self)",
)
async def employee_check_out(
    req: PunchRequest,
    db: AsyncSession = Depends(get_db),
    me: UserModel = Depends(get_current_employee),
):
    await require_staff_permission(me, db, "attendance.checkout")
    service = WorkforceService(db)
    return await service.punch_out(me, req.at)


@router.get(
    "/employee/attendance/today",
    response_model=TodayAttendanceResponse,
    summary="Employee — today's attendance row",
)
async def employee_attendance_today(
    db: AsyncSession = Depends(get_db),
    me: UserModel = Depends(get_current_employee),
):
    service = WorkforceService(db)
    record = await service.today(me)
    return TodayAttendanceResponse(
        record=WorkforceAttendanceDto(**record) if record else None,
        message=None if record else "Not checked in yet today.",
    )


@router.get(
    "/employee/attendance",
    response_model=MyAttendanceResponse,
    summary="Employee — own monthly attendance history",
)
async def employee_attendance_history(
    month: Optional[str] = Query(default=None, description="YYYY-MM; defaults to the store's current month"),
    db: AsyncSession = Depends(get_db),
    me: UserModel = Depends(get_current_employee),
):
    service = WorkforceService(db)
    items, summary = await service.my_month(me, month)
    return MyAttendanceResponse(items=items, summary=summary)


@router.get(
    "/admin/attendance/day",
    response_model=MyAttendanceResponse,
    summary="Supervisor/Admin — attendance rows for one day (bounded)",
    description=(
        "Requires `attendance.view` and account-manager scope (Admin workspace "
        "or a SUPER_EMPLOYEE). Returns every recorded row for the date; empty "
        "means nobody has a row yet — never fabricated rows."
    ),
)
async def admin_attendance_day(
    date: Optional[str] = Query(default=None, description="YYYY-MM-DD; defaults to the store's today"),
    db: AsyncSession = Depends(get_db),
    actor: UserModel = Depends(get_current_account_manager),
):
    await require_staff_permission(actor, db, "attendance.view")
    service = WorkforceService(db)
    try:
        day = date_t.fromisoformat(date) if date else rules.day_of(rules.store_now())
    except ValueError:
        raise BusinessLogicException("date must be YYYY-MM-DD")
    rows = await service.day_roster(day, await service.settings())
    return MyAttendanceResponse(
        items=rows,
        summary={
            "present": sum(1 for r in rows if r["status"] in ("PRESENT", "LATE")),
            "late": sum(1 for r in rows if r["status"] == "LATE"),
            "halfDay": sum(1 for r in rows if r["status"] == "HALF_DAY"),
            "onLeave": sum(1 for r in rows if r["status"] == "LEAVE"),
            "absent": sum(1 for r in rows if r["status"] == "ABSENT"),
            "other": sum(1 for r in rows if r["status"] not in ("PRESENT", "LATE", "HALF_DAY", "LEAVE", "ABSENT")),
            "totalWorkMinutes": sum(r["workMinutes"] for r in rows),
        },
    )
