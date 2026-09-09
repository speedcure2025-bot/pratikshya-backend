"""
Permissions — API router.

Read-only permission catalogue backed by the existing `permissions` table.

  GET /permissions               → all permissions, grouped by category
  GET /permissions/{code}        → single permission
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.dependencies import get_current_admin, get_db
from app.models.auth.user import UserModel
from app.models.rbac.permission import PermissionModel

router = APIRouter(prefix="/permissions", tags=["Permissions"])


def _permission_dto(p: PermissionModel) -> dict:
    return {
        "id":          p.id,
        "code":        p.code,
        "name":        p.name,
        "category":    p.category,
        "description": p.description,
    }


@router.get("/health", summary="Module health check")
async def health_check():
    return {"module": "permissions", "status": "active"}


@router.get("", summary="List all permissions")
async def list_permissions(
    db: AsyncSession = Depends(get_db),
    _admin: UserModel = Depends(get_current_admin),
):
    result = await db.execute(select(PermissionModel).order_by(PermissionModel.category, PermissionModel.code))
    permissions = result.scalars().all()
    return {
        "items":       [_permission_dto(p) for p in permissions],
        "total":       len(permissions),
        "categories":  sorted({p.category for p in permissions}),
    }


@router.get("/{code}", summary="Get a single permission by code")
async def get_permission(
    code: str,
    db: AsyncSession = Depends(get_db),
    _admin: UserModel = Depends(get_current_admin),
):
    permission = (
        await db.execute(select(PermissionModel).where(PermissionModel.code == code))
    ).scalars().first()
    if not permission:
        raise NotFoundException(f"Permission '{code}' not found.")
    return _permission_dto(permission)
