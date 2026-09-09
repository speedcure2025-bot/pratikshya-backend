"""
OrderStatusHistoryModel — append-only status transition log for an order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.orders.order import OrderModel


class OrderStatusHistoryModel(Base):
    """Database model for OrderStatusHistory."""

    __tablename__ = "orders_order_status_history"

    order_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("orders_order.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    to_status: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    actor_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Relationships ─────────────────────────────────────────────────────────
    order: Mapped["OrderModel"] = relationship("OrderModel", back_populates="status_history")
