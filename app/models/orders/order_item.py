"""
OrderItemModel — a single line in an order, snapshot of the product at order time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.orders.order import OrderModel


class OrderItemModel(Base):
    """Database model for OrderItem."""

    __tablename__ = "orders_order_item"

    order_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("orders_order.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # ── Catalog reference ─────────────────────────────────────────────────────
    product_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)  # snapshot
    product_image: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    sku: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # ── Variant selections ────────────────────────────────────────────────────
    color: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    size: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # ── Pricing snapshot ──────────────────────────────────────────────────────
    unit_price: Mapped[int] = mapped_column(Integer, nullable=False)   # price at order time
    original_price: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    line_total: Mapped[int] = mapped_column(Integer, nullable=False)   # unit_price * quantity

    # ── Return tracking ───────────────────────────────────────────────────────
    returned_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── Relationships ─────────────────────────────────────────────────────────
    order: Mapped["OrderModel"] = relationship("OrderModel", back_populates="items")
