"""
Inventory Stock — API router.

  Admin
  ──────────────────────────────────────────────────────────────
  GET  /admin/inventory/stock            ← list stock (filter: warehouse, low_stock, sku)
  GET  /admin/inventory/stock/{id}       ← single stock item
  POST /admin/inventory/stock/adjust     ← adjust stock: { stockId, delta, type, reason }
  GET  /admin/inventory/movements        ← movement ledger (filter: stock_id, type, dates)
  GET  /admin/inventory/low-stock        ← items where available <= low_threshold
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_admin, get_db, require_admin_permission
from app.models.auth.user import UserModel
from app.schemas.inventory.stock import (
    MovementListResponse,
    SingleStockResponse,
    StockAdjustRequest,
    StockItemResponse,
    StockListResponse,
)
from app.services.inventory.inventory_service import InventoryService

router = APIRouter(prefix="/inventory", tags=["Inventory Stock"])


# ===========================================================================
# Health check (kept for backwards compatibility)
# ===========================================================================

@router.get("/health", summary="Module health check", include_in_schema=False)
async def health_check():
    return {"module": "inventory", "status": "active"}


# ===========================================================================
# ADMIN — List stock
# ===========================================================================

@router.get(
    "/admin/inventory/stock",
    response_model=StockListResponse,
    summary="Admin — list all stock items",
    description=(
        "Authorization: `inventory.view`.  \n"
        "Filters: `warehouse_id`, `low_stock` (boolean), `sku` (partial match).  \n"
        "Paginated (`page`, `pageSize`)."
    ),
)
async def admin_list_stock(
    warehouse_id: Optional[str] = Query(None, alias="warehouseId"),
    low_stock: bool = Query(False, alias="lowStock"),
    sku: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100, alias="pageSize"),
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "inventory.view")
    service = InventoryService(db)
    result = await service.list_stock(
        warehouse_id=warehouse_id,
        low_stock=low_stock,
        sku=sku,
        page=page,
        page_size=page_size,
    )
    return StockListResponse(
        items=result["items"],
        total=result["total"],
        page=result["page"],
        page_size=result["page_size"],
    )


# ===========================================================================
# ADMIN — Get single stock item
# ===========================================================================

@router.get(
    "/admin/inventory/stock/{stock_id}",
    response_model=SingleStockResponse,
    summary="Admin — get a single stock item",
    description="Authorization: `inventory.view`.",
)
async def admin_get_stock_item(
    stock_id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "inventory.view")
    service = InventoryService(db)
    item = await service.get_stock_item(stock_id)
    return SingleStockResponse(item=StockItemResponse(**item))


# ===========================================================================
# ADMIN — Adjust stock
# ===========================================================================

@router.post(
    "/admin/inventory/stock/adjust",
    response_model=SingleStockResponse,
    summary="Admin — adjust stock quantity",
    description=(
        "Authorization: `inventory.manage`.  \n"
        "Body: `{ stockId, delta, type, reason? }`.  \n"
        "Positive `delta` increases on-hand; negative decreases it.  \n"
        "`type` is the movement type: `ADJUST`, `RECEIVE`, `DAMAGE`, `RETURN`."
    ),
)
async def admin_adjust_stock(
    req: StockAdjustRequest,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "inventory.manage")
    service = InventoryService(db)
    item = await service.adjust_stock(
        stock_id=req.stock_id,
        delta=req.delta,
        movement_type=req.type,
        reason=req.reason,
        actor_id=current_user.id,
        actor_name=getattr(current_user, "full_name", None),
    )
    return SingleStockResponse(item=StockItemResponse(**item))


# ===========================================================================
# ADMIN — Movement ledger
# ===========================================================================

@router.get(
    "/admin/inventory/movements",
    response_model=MovementListResponse,
    summary="Admin — stock movement ledger",
    description=(
        "Authorization: `inventory.view`.  \n"
        "Filters: `stockId`, `type`, `dateFrom` (ISO-8601), `dateTo` (ISO-8601).  \n"
        "Results ordered newest-first."
    ),
)
async def admin_list_movements(
    stock_id: Optional[str] = Query(None, alias="stockId"),
    movement_type: Optional[str] = Query(None, alias="type"),
    date_from: Optional[datetime] = Query(None, alias="dateFrom"),
    date_to: Optional[datetime] = Query(None, alias="dateTo"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100, alias="pageSize"),
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "inventory.view")
    service = InventoryService(db)
    result = await service.list_movements(
        stock_id=stock_id,
        movement_type=movement_type,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )
    return MovementListResponse(
        movements=result["movements"],
        total=result["total"],
        page=result["page"],
        page_size=result["page_size"],
    )


# ===========================================================================
# ADMIN — Low stock
# ===========================================================================

@router.get(
    "/admin/inventory/low-stock",
    response_model=StockListResponse,
    summary="Admin — list low-stock items",
    description=(
        "Authorization: `inventory.view`.  \n"
        "Returns stock items where `available <= low_threshold`.  \n"
        "Optional filter: `warehouseId`."
    ),
)
async def admin_list_low_stock(
    warehouse_id: Optional[str] = Query(None, alias="warehouseId"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100, alias="pageSize"),
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "inventory.view")
    service = InventoryService(db)
    result = await service.list_low_stock(
        warehouse_id=warehouse_id,
        page=page,
        page_size=page_size,
    )
    return StockListResponse(
        items=result["items"],
        total=result["total"],
        page=result["page"],
        page_size=result["page_size"],
    )
