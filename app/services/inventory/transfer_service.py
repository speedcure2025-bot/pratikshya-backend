"""
Transfer service — warehouse-to-warehouse stock transfers.
"""

from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessLogicException, NotFoundException
from app.models.inventory.stock_transfer import StockTransferModel

# Allowed forward transitions
_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT":       {"REQUESTED", "CANCELLED"},
    "REQUESTED":   {"APPROVED", "CANCELLED"},
    "APPROVED":    {"IN_TRANSIT", "CANCELLED"},
    "IN_TRANSIT":  {"COMPLETED"},
    "COMPLETED":   set(),
    "CANCELLED":   set(),
}

_CANCELLABLE = {"DRAFT", "REQUESTED"}


class TransferService:
    """Business logic service for stock transfers."""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    # -----------------------------------------------------------------------
    # List
    # -----------------------------------------------------------------------

    async def list_transfers(
        self,
        *,
        status: Optional[str] = None,
        from_warehouse_id: Optional[str] = None,
        to_warehouse_id: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        stmt = select(StockTransferModel).order_by(
            StockTransferModel.created_at.desc()
        )

        if status:
            stmt = stmt.where(StockTransferModel.status == status)
        if from_warehouse_id:
            stmt = stmt.where(
                StockTransferModel.from_warehouse_id == from_warehouse_id
            )
        if to_warehouse_id:
            stmt = stmt.where(
                StockTransferModel.to_warehouse_id == to_warehouse_id
            )

        from sqlalchemy import func, select as sel

        count_stmt = sel(func.count()).select_from(stmt.subquery())
        total = (await self.db.execute(count_stmt)).scalar_one()

        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        rows = (await self.db.execute(stmt)).scalars().all()

        return {
            "transfers": [self._to_dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    # -----------------------------------------------------------------------
    # Create
    # -----------------------------------------------------------------------

    async def create_transfer(
        self,
        *,
        from_warehouse_id: str,
        to_warehouse_id: str,
        lines: list[dict],
        notes: Optional[str],
        created_by: Optional[str],
    ) -> dict[str, Any]:
        if from_warehouse_id == to_warehouse_id:
            raise BusinessLogicException(
                "Source and destination warehouse must be different."
            )
        if not lines:
            raise BusinessLogicException("Transfer must include at least one line.")

        transfer = StockTransferModel(
            from_warehouse_id=from_warehouse_id,
            to_warehouse_id=to_warehouse_id,
            status="DRAFT",
            notes=notes,
            created_by=created_by,
            lines=lines,
        )
        self.db.add(transfer)
        await self.db.flush()

        return self._to_dict(transfer)

    # -----------------------------------------------------------------------
    # Complete (mark RECEIVED)
    # -----------------------------------------------------------------------

    async def complete_transfer(self, transfer_id: str) -> dict[str, Any]:
        transfer = await self._get_or_404(transfer_id)
        return await self._transition(transfer, "COMPLETED")

    # -----------------------------------------------------------------------
    # Cancel
    # -----------------------------------------------------------------------

    async def cancel_transfer(self, transfer_id: str) -> dict[str, Any]:
        transfer = await self._get_or_404(transfer_id)
        if transfer.status not in _CANCELLABLE:
            raise BusinessLogicException(
                f"Transfer in status '{transfer.status}' cannot be cancelled. "
                f"Only DRAFT and REQUESTED transfers may be cancelled."
            )
        return await self._transition(transfer, "CANCELLED")

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    async def _get_or_404(self, transfer_id: str) -> StockTransferModel:
        row = (
            await self.db.execute(
                select(StockTransferModel).where(
                    StockTransferModel.id == transfer_id
                )
            )
        ).scalars().first()
        if not row:
            raise NotFoundException(f"Transfer '{transfer_id}' not found.")
        return row

    async def _transition(
        self, transfer: StockTransferModel, new_status: str
    ) -> dict[str, Any]:
        allowed = _TRANSITIONS.get(transfer.status, set())
        if new_status not in allowed:
            raise BusinessLogicException(
                f"Cannot move transfer from '{transfer.status}' to '{new_status}'."
            )
        transfer.status = new_status
        await self.db.flush()
        return self._to_dict(transfer)

    @staticmethod
    def _to_dict(row: StockTransferModel) -> dict[str, Any]:
        # transfer_number derived from id prefix (no dedicated DB column)
        transfer_number = f"TRF-{row.id[:8].upper()}"
        return {
            "id": row.id,
            "transfer_number": transfer_number,
            "from_warehouse_id": row.from_warehouse_id,
            "to_warehouse_id": row.to_warehouse_id,
            "status": row.status,
            "notes": row.notes,
            "created_by": row.created_by,
            "lines": row.lines or [],
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
