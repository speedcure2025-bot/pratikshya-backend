from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_employee, get_db
from app.models.auth.user import UserModel
from app.models.employee.support_case import SupportCaseModel
from app.schemas.employee.support import (
    SupportCaseCreateRequest,
    SupportCaseListResponse,
    SupportCaseResponse,
    SupportCaseUpdateRequest,
)

router = APIRouter(prefix="/employee/support/cases", tags=["Employee Support Desk"])


@router.get(
    "",
    response_model=SupportCaseListResponse,
    summary="List customer support tickets for employee desk",
)
async def list_support_cases(
    status_filter: Optional[str] = Query(None, alias="status"),
    priority_filter: Optional[str] = Query(None, alias="priority"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_employee),
):
    query = select(SupportCaseModel)

    if status_filter:
        query = query.where(SupportCaseModel.status == status_filter.upper())
    if priority_filter:
        query = query.where(SupportCaseModel.priority == priority_filter.upper())

    count_stmt = select(func.count()).select_from(query.subquery())
    total_res = await db.execute(count_stmt)
    total = total_res.scalar() or 0

    query = query.order_by(SupportCaseModel.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    res = await db.execute(query)
    rows = res.scalars().all()

    return {
        "ok": True,
        "items": [SupportCaseResponse.model_validate(r) for r in rows],
        "total": total,
    }


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create new support case",
)
async def create_support_case(
    payload: SupportCaseCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_employee),
):
    case_entry = SupportCaseModel(
        customer_id=payload.customerId,
        subject=payload.subject,
        description=payload.description,
        priority=payload.priority.upper() if payload.priority else "MEDIUM",
        status="OPEN",
        assigned_employee_id=current_user.id,
    )
    db.add(case_entry)
    await db.commit()
    await db.refresh(case_entry)

    return {
        "ok": True,
        "case": SupportCaseResponse.model_validate(case_entry),
    }


@router.patch(
    "/{case_id}",
    summary="Update support case status/assignee/notes",
)
async def update_support_case(
    case_id: str,
    payload: SupportCaseUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_employee),
):
    stmt = select(SupportCaseModel).where(SupportCaseModel.id == case_id)
    res = await db.execute(stmt)
    case_entry = res.scalars().first()
    if not case_entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Support case not found")

    if payload.status:
        case_entry.status = payload.status.upper()
    if payload.resolutionNotes:
        case_entry.resolution_notes = payload.resolutionNotes
    if payload.assignedEmployeeId:
        case_entry.assigned_employee_id = payload.assignedEmployeeId

    await db.commit()
    await db.refresh(case_entry)

    return {
        "ok": True,
        "case": SupportCaseResponse.model_validate(case_entry),
    }
