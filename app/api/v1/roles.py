"""
Roles — API router.

Admin-scoped role catalogue backed by the existing `roles`, `user_roles`
and `role_permissions` tables. No schema changes; read-only for this phase.

  GET /roles                 → all roles (admin)
  GET /roles/{role_id}       → single role with its permission codes
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.dependencies import get_current_admin, get_db
from app.models.auth.user import UserModel
from app.models.rbac.permission import PermissionModel
from app.models.rbac.role import RoleModel
from app.models.rbac.role_permission import RolePermissionModel

router = APIRouter(prefix="/roles", tags=["Roles"])


def _role_dto(role: RoleModel) -> dict:
    return {
        "id":         role.id,
        "name":       role.name,
        "description": role.description,
        "isSystem":   role.is_system,
        "createdAt":  role.created_at.isoformat() if role.created_at else None,
    }


@router.get("/health", summary="Module health check")
async def health_check():
    return {"module": "roles", "status": "active"}


@router.get("", summary="List roles")
async def list_roles(
    db: AsyncSession = Depends(get_db),
    _admin: UserModel = Depends(get_current_admin),
):
    result = await db.execute(select(RoleModel).order_by(RoleModel.name))
    roles = result.scalars().all()
    return {"items": [_role_dto(r) for r in roles], "total": len(roles)}


@router.get("/{role_id}", summary="Get a single role with permission codes")
async def get_role(
    role_id: str,
    db: AsyncSession = Depends(get_db),
    _admin: UserModel = Depends(get_current_admin),
):
    role = (await db.execute(select(RoleModel).where(RoleModel.id == role_id))).scalars().first()
    if not role:
        raise NotFoundException(f"Role '{role_id}' not found.")

    permission_rows = (
        await db.execute(
            select(PermissionModel.code)
            .join(RolePermissionModel, RolePermissionModel.permission_id == PermissionModel.id)
            .where(RolePermissionModel.role_id == role_id)
            .order_by(PermissionModel.code)
        )
    ).scalars().all()

    return {**_role_dto(role), "permissionCodes": list(permission_rows)}
