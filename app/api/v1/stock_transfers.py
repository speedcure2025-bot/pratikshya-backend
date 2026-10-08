"""
Stock Transfers — API router.

  Admin
  ──────────────────────────────────────────────────────────────
  GET  /admin/stock-transfers              ← list all transfers
  POST /admin/stock-transfers              ← create: { fromWarehouseId, toWarehouseId, lines, notes? }
  POST /admin/stock-transfers/{id}/complete ← mark as received
  POST /admin/stock-transfers/{id}/cancel  ← cancel DRAFT/REQUESTED transfers
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_admin, get_db, require_admin_permission
from app.models.auth.user import UserModel
from app.schemas.inventory.transfer import (
    SingleTransferResponse,
    TransferCreate,
    TransferListResponse,
    TransferResponse,
)
from app.services.inventory.transfer_service import TransferService

router = APIRouter(prefix="/stock-transfers", tags=["Stock Transfers"])


# ===========================================================================
# Health check (kept for backwards compatibility)
# ===========================================================================

@router.get("/health", summary="Module health check", include_in_schema=False)
async def health_check():
    return {"module": "stock_transfers", "status": "active"}


# ===========================================================================
# ADMIN — List transfers
# ===========================================================================

@router.get(
    "/admin/stock-transfers",
    response_model=TransferListResponse,
    summary="Admin — list stock transfers",
    description=(
        "Authorization: `inventory.view`.  \n"
        "Filters: `status`, `fromWarehouseId`, `toWarehouseId`.  \n"
        "Results ordered newest-first."
    ),
)
async def admin_list_transfers(
    transfer_status: Optional[str] = Query(None, alias="status"),
    from_warehouse_id: Optional[str] = Query(None, alias="fromWarehouseId"),
    to_warehouse_id: Optional[str] = Query(None, alias="toWarehouseId"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100, alias="pageSize"),
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "inventory.view")
    service = TransferService(db)
    result = await service.list_transfers(
        status=transfer_status,
        from_warehouse_id=from_warehouse_id,
        to_warehouse_id=to_warehouse_id,
        page=page,
        page_size=page_size,
    )
    return TransferListResponse(
        transfers=result["transfers"],
        total=result["total"],
        page=result["page"],
        page_size=result["page_size"],
    )


# ===========================================================================
# ADMIN — Create transfer
# ===========================================================================

@router.post(
    "/admin/stock-transfers",
    response_model=SingleTransferResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Admin — create a stock transfer",
    description=(
        "Authorization: `inventory.manage`.  \n"
        "Body: `{ fromWarehouseId, toWarehouseId, lines: [{ stockId?, sku?, quantity }], notes? }`.  \n"
        "Transfer starts in DRAFT status."
    ),
)
async def admin_create_transfer(
    req: TransferCreate,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "inventory.manage")
    service = TransferService(db)
    transfer = await service.create_transfer(
        from_warehouse_id=req.from_warehouse_id,
        to_warehouse_id=req.to_warehouse_id,
        lines=[line.model_dump(by_alias=False) for line in req.lines],
        notes=req.notes,
        created_by=current_user.id,
    )
    return SingleTransferResponse(transfer=TransferResponse(**transfer))


# ===========================================================================
# ADMIN — Complete transfer
# ===========================================================================

@router.post(
    "/admin/stock-transfers/{transfer_id}/complete",
    response_model=SingleTransferResponse,
    summary="Admin — mark transfer as received (COMPLETED)",
    description="Authorization: `inventory.manage`.",
)
async def admin_complete_transfer(
    transfer_id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "inventory.manage")
    service = TransferService(db)
    transfer = await service.complete_transfer(transfer_id)
    return SingleTransferResponse(transfer=TransferResponse(**transfer))


# ===========================================================================
# ADMIN — Cancel transfer
# ===========================================================================

@router.post(
    "/admin/stock-transfers/{transfer_id}/cancel",
    response_model=SingleTransferResponse,
    summary="Admin — cancel a DRAFT or REQUESTED transfer",
    description=(
        "Authorization: `inventory.manage`.  \n"
        "Only DRAFT and REQUESTED transfers may be cancelled."
    ),
)
async def admin_cancel_transfer(
    transfer_id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "inventory.manage")
    service = TransferService(db)
    transfer = await service.cancel_transfer(transfer_id)
    return SingleTransferResponse(transfer=TransferResponse(**transfer))
