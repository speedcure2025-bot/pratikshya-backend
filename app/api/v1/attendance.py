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

Access rules: employees reach ONLY their own records (the /employee/* routes
read the identity from the token and never accept an employee id). The
/admin/* routes below are admin-account only (SUPER_ADMIN and ADMIN see every
employee).

Machine management (admin accounts, `attendance.manage`):

  GET   /admin/attendance/devices                       → registered punching machines
  POST  /admin/attendance/devices          {serialNumber,label}
  PATCH /admin/attendance/devices/{id}     {label?,isActive?}
  GET   /admin/attendance/unmapped-punches              → machine IDs not linked to anyone
  PUT   /admin/attendance/device-pin/{employee}  {devicePin}  → link a machine ID
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
    DeviceCreateRequest,
    DeviceDto,
    DevicePinRequest,
    DevicePinResult,
    DeviceUpdateRequest,
    MyAttendanceResponse,
    PunchRequest,
    PunchResult,
    TodayAttendanceResponse,
    UnmappedPinDto,
    WorkforceAttendanceDto,
)
from app.services.employee import workforce_rules as rules
from app.services.employee.attendance_ingest_service import AttendanceIngestService
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
    # Canonical camelCase code (rbac.EMPLOYEE_SELF_SERVICE_PERMISSIONS). The old
    # lowercase spelling never matched a granted code, so non-wildcard
    # employees were refused.
    await require_staff_permission(me, db, "attendance.checkIn")
    service = WorkforceService(db)
    return await service.punch_in(me)


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
    await require_staff_permission(me, db, "attendance.checkOut")
    service = WorkforceService(db)
    return await service.punch_out(me)


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
        "Requires `attendance.view` on an Admin account (SUPER_ADMIN or ADMIN). "
        "Returns every recorded row for the date plus computed ABSENT / "
        "NOT_CHECKED_IN / LEAVE rows (`synthetic: true`) for staff with no record."
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
    # Finished days that still lack a check-out are flagged for correction.
    await AttendanceIngestService(db).sweep_open_days()
    rows = await service.day_roster(day, await service.settings())
    return MyAttendanceResponse(
        items=rows,
        summary={
            "present": sum(1 for r in rows if r["status"] in ("PRESENT", "LATE", "ON_DUTY")),
            "late": sum(1 for r in rows if r["status"] == "LATE"),
            "halfDay": sum(1 for r in rows if r["status"] == "HALF_DAY"),
            "onLeave": sum(1 for r in rows if r["status"] == "LEAVE"),
            "absent": sum(1 for r in rows if r["status"] == "ABSENT"),
            "notCheckedIn": sum(1 for r in rows if r["status"] == "NOT_CHECKED_IN"),
            "pendingCorrection": sum(1 for r in rows if r["status"] == "PENDING_CORRECTION"),
            "other": sum(
                1
                for r in rows
                if r["status"]
                not in (
                    "PRESENT", "LATE", "ON_DUTY", "HALF_DAY", "LEAVE", "ABSENT",
                    "NOT_CHECKED_IN", "PENDING_CORRECTION",
                )
            ),
            "totalWorkMinutes": sum(r["workMinutes"] for r in rows),
        },
    )


# ── Punching machines (admin accounts) ─────────────────────────────────────────────────


@router.get(
    "/admin/attendance/devices",
    response_model=list[DeviceDto],
    summary="Admin — registered punching machines",
)
async def admin_list_devices(
    db: AsyncSession = Depends(get_db),
    actor: UserModel = Depends(get_current_account_manager),
):
    await require_staff_permission(actor, db, "attendance.manage")
    return await AttendanceIngestService(db).list_devices()


@router.post(
    "/admin/attendance/devices",
    response_model=DeviceDto,
    status_code=201,
    summary="Admin — register a punching machine by serial number",
)
async def admin_create_device(
    req: DeviceCreateRequest,
    db: AsyncSession = Depends(get_db),
    actor: UserModel = Depends(get_current_account_manager),
):
    await require_staff_permission(actor, db, "attendance.manage")
    return await AttendanceIngestService(db).create_device(req.serialNumber, req.label, actor.id)


@router.patch(
    "/admin/attendance/devices/{device_id}",
    response_model=DeviceDto,
    summary="Admin — rename or switch a machine on/off",
)
async def admin_update_device(
    device_id: str,
    req: DeviceUpdateRequest,
    db: AsyncSession = Depends(get_db),
    actor: UserModel = Depends(get_current_account_manager),
):
    await require_staff_permission(actor, db, "attendance.manage")
    return await AttendanceIngestService(db).update_device(device_id, req.label, req.isActive, actor.id)


@router.get(
    "/admin/attendance/unmapped-punches",
    response_model=list[UnmappedPinDto],
    summary="Admin — machine IDs that punched but are not linked to an employee",
)
async def admin_unmapped_punches(
    db: AsyncSession = Depends(get_db),
    actor: UserModel = Depends(get_current_account_manager),
):
    await require_staff_permission(actor, db, "attendance.manage")
    return await AttendanceIngestService(db).unmapped_pins()


@router.put(
    "/admin/attendance/device-pin/{employee_id}",
    response_model=DevicePinResult,
    summary="Admin — link (or clear) an employee's machine ID",
    description=(
        "Linking attaches every earlier unmapped punch with that machine ID to the "
        "employee and rebuilds the affected days."
    ),
)
async def admin_set_device_pin(
    employee_id: str,
    req: DevicePinRequest,
    db: AsyncSession = Depends(get_db),
    actor: UserModel = Depends(get_current_account_manager),
):
    await require_staff_permission(actor, db, "attendance.manage")
    return await AttendanceIngestService(db).set_device_pin(employee_id, req.devicePin, actor.id)
