"""
ReturnOrderModel — a return request against a delivered order.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import JSON

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.orders.order import OrderModel
    from app.models.orders.return_item import ReturnItemModel


class ReturnOrderModel(Base):
    """Database model for ReturnOrder."""

    __tablename__ = "orders_return_order"

    order_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("orders_order.id", ondelete="CASCADE"), nullable=False, index=True
    )

    return_number: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)

    # ── Customer info snapshot ────────────────────────────────────────────────
    customer_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)

    # ── Return config ─────────────────────────────────────────────────────────
    pickup_method: Mapped[str] = mapped_column(String(30), nullable=False, default="SCHEDULED_PICKUP")
    # SCHEDULED_PICKUP | CUSTOMER_DROP_OFF

    # ── Status ────────────────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="RETURN_REQUESTED", index=True)
    # RETURN_REQUESTED | APPROVED | REJECTED | PICKUP_SCHEDULED | RECEIVED | INSPECTED
    # | REFUND_INITIATED | REFUNDED

    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rejection_reason_customer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Inspection ────────────────────────────────────────────────────────────
    package_condition: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    inspection_condition: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    inspection_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Refund ────────────────────────────────────────────────────────────────
    refund_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    refund_status: Mapped[str] = mapped_column(String(30), nullable=False, default="NOT_REQUESTED")
    refund_method: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    refund_initiated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    refund_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Pickup scheduling ─────────────────────────────────────────────────────
    pickup_scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    pickup_address: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # ── Timeline ──────────────────────────────────────────────────────────────
    timeline: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, default=list)

    # ── Review info ───────────────────────────────────────────────────────────
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Relationships ─────────────────────────────────────────────────────────
    order: Mapped["OrderModel"] = relationship("OrderModel", back_populates="returns")
    items: Mapped[List["ReturnItemModel"]] = relationship(
        "ReturnItemModel", back_populates="return_order", cascade="all, delete-orphan"
    )
