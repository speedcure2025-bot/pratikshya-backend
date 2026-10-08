"""
Warehouses — API router.

  Admin
  ──────────────────────────────────────────────────────────────
  GET   /admin/warehouses        ← list all warehouses
  POST  /admin/warehouses        ← create: { name, code, address?, type? }
  PATCH /admin/warehouses/{id}   ← update: { name?, address?, is_active? }
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_admin, get_db, require_admin_permission
from app.models.auth.user import UserModel
from app.schemas.inventory.warehouse import (
    SingleWarehouseResponse,
    WarehouseCreate,
    WarehouseListResponse,
    WarehouseResponse,
    WarehouseUpdate,
)
from app.services.inventory.warehouse_service import WarehouseService

router = APIRouter(prefix="/warehouses", tags=["Warehouses"])


# ===========================================================================
# Health check (kept for backwards compatibility)
# ===========================================================================

@router.get("/health", summary="Module health check", include_in_schema=False)
async def health_check():
    return {"module": "warehouses", "status": "active"}


# ===========================================================================
# ADMIN — List warehouses
# ===========================================================================

@router.get(
    "/admin/warehouses",
    response_model=WarehouseListResponse,
    summary="Admin — list all warehouses",
    description=(
        "Authorization: `inventory.view`.  \n"
        "Optional `includeInactive=true` to include disabled warehouses."
    ),
)
async def admin_list_warehouses(
    include_inactive: bool = Query(False, alias="includeInactive"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200, alias="pageSize"),
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "inventory.view")
    service = WarehouseService(db)
    result = await service.list_warehouses(
        include_inactive=include_inactive,
        page=page,
        page_size=page_size,
    )
    return WarehouseListResponse(
        warehouses=result["warehouses"],
        total=result["total"],
        page=result["page"],
        page_size=result["page_size"],
    )


# ===========================================================================
# ADMIN — Create warehouse
# ===========================================================================

@router.post(
    "/admin/warehouses",
    response_model=SingleWarehouseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Admin — create a warehouse",
    description=(
        "Authorization: `inventory.manage`.  \n"
        "Body: `{ name, code, address?, type? }`. Code must be unique."
    ),
)
async def admin_create_warehouse(
    req: WarehouseCreate,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "inventory.manage")
    service = WarehouseService(db)
    warehouse = await service.create_warehouse(
        name=req.name,
        code=req.code,
        address=req.address,
        warehouse_type=req.type or "WAREHOUSE",
    )
    return SingleWarehouseResponse(warehouse=WarehouseResponse(**warehouse))


# ===========================================================================
# ADMIN — Update warehouse
# ===========================================================================

@router.patch(
    "/admin/warehouses/{warehouse_id}",
    response_model=SingleWarehouseResponse,
    summary="Admin — update warehouse",
    description=(
        "Authorization: `inventory.manage`.  \n"
        "Body: `{ name?, address?, is_active? }`. Omitted fields are unchanged."
    ),
)
async def admin_update_warehouse(
    warehouse_id: str,
    req: WarehouseUpdate,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "inventory.manage")
    service = WarehouseService(db)
    warehouse = await service.update_warehouse(
        warehouse_id,
        name=req.name,
        address=req.address,
        is_active=req.is_active,
    )
    return SingleWarehouseResponse(warehouse=WarehouseResponse(**warehouse))
