"""
Inventory service — stock, movements, low-stock queries.
"""

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessLogicException, NotFoundException
from app.models.inventory.inventory_movement import InventoryMovementModel
from app.models.inventory.inventory_stock import InventoryStockModel


class InventoryService:
    """Business logic service for inventory stock management."""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    # -----------------------------------------------------------------------
    # Stock
    # -----------------------------------------------------------------------

    async def list_stock(
        self,
        *,
        warehouse_id: Optional[str] = None,
        low_stock: bool = False,
        sku: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        stmt = select(InventoryStockModel)

        if warehouse_id:
            stmt = stmt.where(InventoryStockModel.warehouse_id == warehouse_id)
        if low_stock:
            stmt = stmt.where(
                InventoryStockModel.available <= InventoryStockModel.low_threshold
            )
        if sku:
            stmt = stmt.where(InventoryStockModel.sku.ilike(f"%{sku}%"))

        # Total count (re-uses same filters)
        from sqlalchemy import func, select as sel

        count_stmt = sel(func.count()).select_from(stmt.subquery())
        total = (await self.db.execute(count_stmt)).scalar_one()

        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        rows = (await self.db.execute(stmt)).scalars().all()

        return {
            "items": [self._stock_to_dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def get_stock_item(self, stock_id: str) -> dict[str, Any]:
        row = await self._get_stock_or_404(stock_id)
        return self._stock_to_dict(row)

    async def adjust_stock(
        self,
        *,
        stock_id: str,
        delta: int,
        movement_type: str,
        reason: Optional[str],
        actor_id: Optional[str],
        actor_name: Optional[str],
    ) -> dict[str, Any]:
        if delta == 0:
            raise BusinessLogicException("Delta must be non-zero.")

        stock = await self._get_stock_or_404(stock_id)

        new_on_hand = stock.on_hand + delta
        if new_on_hand < 0:
            raise BusinessLogicException(
                f"Adjustment would result in negative on-hand quantity "
                f"({stock.on_hand} + {delta} = {new_on_hand})."
            )

        stock.on_hand = new_on_hand
        stock.available = max(0, new_on_hand - stock.reserved)

        movement = InventoryMovementModel(
            stock_id=stock_id,
            delta=delta,
            type=movement_type,
            reason=reason,
            actor_id=actor_id,
            actor_name=actor_name,
        )
        self.db.add(movement)
        await self.db.flush()

        return self._stock_to_dict(stock)

    # -----------------------------------------------------------------------
    # Movements
    # -----------------------------------------------------------------------

    async def list_movements(
        self,
        *,
        stock_id: Optional[str] = None,
        movement_type: Optional[str] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        stmt = select(InventoryMovementModel).order_by(
            InventoryMovementModel.created_at.desc()
        )

        if stock_id:
            stmt = stmt.where(InventoryMovementModel.stock_id == stock_id)
        if movement_type:
            stmt = stmt.where(InventoryMovementModel.type == movement_type)
        if date_from:
            stmt = stmt.where(InventoryMovementModel.created_at >= date_from)
        if date_to:
            stmt = stmt.where(InventoryMovementModel.created_at <= date_to)

        from sqlalchemy import func, select as sel

        count_stmt = sel(func.count()).select_from(stmt.subquery())
        total = (await self.db.execute(count_stmt)).scalar_one()

        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        rows = (await self.db.execute(stmt)).scalars().all()

        return {
            "movements": [self._movement_to_dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    # -----------------------------------------------------------------------
    # Low stock
    # -----------------------------------------------------------------------

    async def list_low_stock(
        self,
        *,
        warehouse_id: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        return await self.list_stock(
            warehouse_id=warehouse_id,
            low_stock=True,
            page=page,
            page_size=page_size,
        )

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    async def _get_stock_or_404(self, stock_id: str) -> InventoryStockModel:
        row = (
            await self.db.execute(
                select(InventoryStockModel).where(InventoryStockModel.id == stock_id)
            )
        ).scalars().first()
        if not row:
            raise NotFoundException(f"Stock item '{stock_id}' not found.")
        return row

    @staticmethod
    def _stock_to_dict(row: InventoryStockModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "product_id": row.product_id,
            "variant_id": row.variant_id,
            "sku": row.sku,
            "warehouse_id": row.warehouse_id,
            "quantity_on_hand": row.on_hand,
            "quantity_reserved": row.reserved,
            "quantity_available": row.available,
            "low_threshold": row.low_threshold,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _movement_to_dict(row: InventoryMovementModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "stock_id": row.stock_id,
            "movement_type": row.type,
            "quantity_change": row.delta,
            "reason": row.reason,
            "actor_id": row.actor_id,
            "actor_name": row.actor_name,
            "created_at": row.created_at,
        }
