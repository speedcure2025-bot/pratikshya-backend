from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_employee, get_db
from app.models.auth.user import UserModel
from app.models.employee.styling_appointment import StylingAppointmentModel
from app.schemas.employee.styling import (
    StylingAppointmentListResponse,
    StylingAppointmentResponse,
)

router = APIRouter(prefix="/employee/styling", tags=["Employee Styling Desk"])


@router.get(
    "/appointments",
    response_model=StylingAppointmentListResponse,
    summary="List styling appointments",
)
async def list_styling_appointments(
    appointment_date: Optional[date] = Query(None, alias="date"),
    status_filter: Optional[str] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_employee),
):
    query = select(StylingAppointmentModel)

    if appointment_date:
        query = query.where(StylingAppointmentModel.appointment_date == appointment_date)
    if status_filter:
        query = query.where(StylingAppointmentModel.status == status_filter.upper())

    count_stmt = select(func.count()).select_from(query.subquery())
    total_res = await db.execute(count_stmt)
    total = total_res.scalar() or 0

    query = query.order_by(StylingAppointmentModel.appointment_date.desc()).offset((page - 1) * page_size).limit(page_size)
    res = await db.execute(query)
    rows = res.scalars().all()

    return {
        "ok": True,
        "items": [StylingAppointmentResponse.model_validate(r) for r in rows],
        "total": total,
    }


@router.get(
    "/requests",
    response_model=StylingAppointmentListResponse,
    summary="List incoming styling consultation requests",
)
async def list_styling_requests(
    assigned_employee_id: Optional[str] = Query(None, alias="assignedEmployeeId"),
    status_filter: Optional[str] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_employee),
):
    query = select(StylingAppointmentModel)

    if assigned_employee_id:
        query = query.where(StylingAppointmentModel.assigned_employee_id == assigned_employee_id)
    else:
        query = query.where(
            (StylingAppointmentModel.assigned_employee_id == current_user.id)
            | (StylingAppointmentModel.assigned_employee_id.is_(None))
        )

    if status_filter:
        query = query.where(StylingAppointmentModel.status == status_filter.upper())

    count_stmt = select(func.count()).select_from(query.subquery())
    total_res = await db.execute(count_stmt)
    total = total_res.scalar() or 0

    query = query.order_by(StylingAppointmentModel.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    res = await db.execute(query)
    rows = res.scalars().all()

    return {
        "ok": True,
        "items": [StylingAppointmentResponse.model_validate(r) for r in rows],
        "total": total,
    }
