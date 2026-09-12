"""
Users — API router.

Admin-scoped user directory backed by the existing `users`, `user_roles`
and profile tables. Read-only for this phase.

  GET /users?q=&userType=&status=&page=&pageSize=   → paginated user list (admin)
  GET /users/{user_id}                              → single user (admin)
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.dependencies import get_current_admin, get_db, require_admin_permission
from app.models.auth.user import UserModel
from app.models.customer.customer import CustomerProfileModel
from app.models.employee.employee import EmployeeProfileModel
from app.models.rbac.role import RoleModel
from app.models.rbac.user_role import UserRoleModel

router = APIRouter(prefix="/users", tags=["Users"])


def _user_dto(user: UserModel, customer: Optional[CustomerProfileModel] = None,
              employee: Optional[EmployeeProfileModel] = None,
              role_names: Optional[list] = None) -> dict:
    return {
        "id":                 user.id,
        "fullName":           user.full_name,
        "email":              user.email,
        "phone":              user.phone,
        "userType":           user.user_type,
        "status":             user.status,
        "isVerified":         user.is_verified,
        "forcePasswordChange": user.force_password_change,
        "createdAt":          user.created_at.isoformat() if user.created_at else None,
        "roles":              role_names or [],
        "employeeCode":       employee.employee_code if employee else None,
        "department":         employee.department if employee else None,
        "designation":        employee.designation if employee else None,
        "loyaltyTier":        customer.loyalty_tier if customer else None,
    }


@router.get("/health", summary="Module health check")
async def health_check():
    return {"module": "users", "status": "active"}


@router.get("", summary="List users (admin)")
async def list_users(
    q: Optional[str] = Query(default=None, description="Search name / email / phone"),
    user_type: Optional[str] = Query(default=None),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _admin: UserModel = Depends(get_current_admin),
):
    await require_admin_permission(_admin, db, "users.view")
    stmt = select(UserModel)
    if q:
        term = f"%{q}%"
        stmt = stmt.where(or_(UserModel.full_name.ilike(term), UserModel.email.ilike(term), UserModel.phone.ilike(term)))
    if user_type:
        stmt = stmt.where(UserModel.user_type == user_type)
    if status_filter:
        stmt = stmt.where(UserModel.status == status_filter)

    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    stmt = stmt.order_by(UserModel.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    users = (await db.execute(stmt)).scalars().all()

    # Batch resolution for the page (admin consolidation, HP-7): the page's
    # profiles and roles come back in THREE bounded IN queries instead of
    # three queries per row.
    page_ids = [user.id for user in users]
    customer_by_user: dict = {}
    employee_by_user: dict = {}
    roles_by_user: dict = {}
    if page_ids:
        customer_rows = (
            await db.execute(
                select(CustomerProfileModel).where(CustomerProfileModel.user_id.in_(page_ids))
            )
        ).scalars().all()
        customer_by_user = {row.user_id: row for row in customer_rows}
        employee_rows = (
            await db.execute(
                select(EmployeeProfileModel).where(EmployeeProfileModel.user_id.in_(page_ids))
            )
        ).scalars().all()
        employee_by_user = {row.user_id: row for row in employee_rows}
        role_rows = (
            await db.execute(
                select(UserRoleModel.user_id, RoleModel.name)
                .join(RoleModel, RoleModel.id == UserRoleModel.role_id)
                .where(UserRoleModel.user_id.in_(page_ids))
            )
        ).all()
        for row in role_rows:
            roles_by_user.setdefault(row.user_id, []).append(row.name)

    items = []
    for user in users:
        items.append(
            _user_dto(
                user,
                customer_by_user.get(user.id),
                employee_by_user.get(user.id),
                roles_by_user.get(user.id, []),
            )
        )

    return {"items": items, "total": total, "page": page, "pageSize": page_size}


@router.get("/{user_id}", summary="Get a single user (admin)")
async def get_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    _admin: UserModel = Depends(get_current_admin),
):
    await require_admin_permission(_admin, db, "users.view")
    user = (await db.execute(select(UserModel).where(UserModel.id == user_id))).scalars().first()
    if not user:
        raise NotFoundException(f"User '{user_id}' not found.")

    customer = None
    employee = None
    if user.user_type == "customer":
        customer = (await db.execute(
            select(CustomerProfileModel).where(CustomerProfileModel.user_id == user.id)
        )).scalars().first()
    if user.user_type == "employee":
        employee = (await db.execute(
            select(EmployeeProfileModel).where(EmployeeProfileModel.user_id == user.id)
        )).scalars().first()
    role_rows = (
        await db.execute(
            select(RoleModel.name)
            .join(UserRoleModel, UserRoleModel.role_id == RoleModel.id)
            .where(UserRoleModel.user_id == user.id)
        )
    ).scalars().all()

    return _user_dto(user, customer, employee, list(role_rows))
