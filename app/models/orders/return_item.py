"""
ReturnItemModel — individual line within a return request.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.orders.return_order import ReturnOrderModel


class ReturnItemModel(Base):
    """Database model for ReturnItem."""

    __tablename__ = "orders_return_item"

    return_order_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("orders_return_order.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_item_id: Mapped[str] = mapped_column(String(36), nullable=False)  # ref to OrderItemModel.id
    product_id: Mapped[str] = mapped_column(String(36), nullable=False)
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)

    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Refund amount allocated to this line
    refund_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── Relationships ─────────────────────────────────────────────────────────
    return_order: Mapped["ReturnOrderModel"] = relationship("ReturnOrderModel", back_populates="items")
