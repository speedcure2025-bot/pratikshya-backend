"""
Warehouse service — CRUD for warehouse records.
"""

from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessLogicException, NotFoundException
from app.models.inventory.warehouse import WarehouseModel


class WarehouseService:
    """Business logic service for warehouse management."""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    # -----------------------------------------------------------------------
    # List
    # -----------------------------------------------------------------------

    async def list_warehouses(
        self,
        *,
        include_inactive: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        stmt = select(WarehouseModel).order_by(WarehouseModel.name)
        if not include_inactive:
            stmt = stmt.where(WarehouseModel.is_active.is_(True))

        from sqlalchemy import func, select as sel

        count_stmt = sel(func.count()).select_from(stmt.subquery())
        total = (await self.db.execute(count_stmt)).scalar_one()

        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        rows = (await self.db.execute(stmt)).scalars().all()

        return {
            "warehouses": [self._to_dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    # -----------------------------------------------------------------------
    # Create
    # -----------------------------------------------------------------------

    async def create_warehouse(
        self,
        *,
        name: str,
        code: str,
        address: Optional[str] = None,
        warehouse_type: str = "WAREHOUSE",
    ) -> dict[str, Any]:
        # Code must be unique
        existing = (
            await self.db.execute(
                select(WarehouseModel).where(WarehouseModel.code == code)
            )
        ).scalars().first()
        if existing:
            raise BusinessLogicException(
                f"A warehouse with code '{code}' already exists."
            )

        warehouse = WarehouseModel(
            name=name,
            code=code,
            address=address,
            type=warehouse_type,
            is_active=True,
        )
        self.db.add(warehouse)
        await self.db.flush()
        return self._to_dict(warehouse)

    # -----------------------------------------------------------------------
    # Update
    # -----------------------------------------------------------------------

    async def update_warehouse(
        self,
        warehouse_id: str,
        *,
        name: Optional[str] = None,
        address: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> dict[str, Any]:
        warehouse = await self._get_or_404(warehouse_id)

        if name is not None:
            warehouse.name = name
        if address is not None:
            warehouse.address = address
        if is_active is not None:
            warehouse.is_active = is_active

        await self.db.flush()
        return self._to_dict(warehouse)

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    async def _get_or_404(self, warehouse_id: str) -> WarehouseModel:
        row = (
            await self.db.execute(
                select(WarehouseModel).where(WarehouseModel.id == warehouse_id)
            )
        ).scalars().first()
        if not row:
            raise NotFoundException(f"Warehouse '{warehouse_id}' not found.")
        return row

    @staticmethod
    def _to_dict(row: WarehouseModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "code": row.code,
            "address": row.address,
            "type": row.type,
            "is_active": row.is_active,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
