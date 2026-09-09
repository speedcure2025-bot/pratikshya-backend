"""
OrderModel — persists a placed order with its full lifecycle data.

Key fields mirror the frontend DATABASE_SCHEMA.md / buildOrderRecord() shape:
  - status         : current order status (ORDER_STATUSES)
  - payment_status : current payment status (ORDER_PAYMENT_STATUS)
  - timeline / status_history stored as JSON columns (JSONB in Postgres)
  - delivery/shipping/address info stored as JSON
  - items are in the separate OrderItemModel (one-to-many)
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import JSON

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.orders.order_item import OrderItemModel
    from app.models.orders.order_status_history import OrderStatusHistoryModel
    from app.models.orders.return_order import ReturnOrderModel


class OrderModel(Base):
    """Database model for Order."""

    __tablename__ = "orders_order"

    # ── Order number (human-readable, e.g. PF-ORD-000042) ────────────────────
    order_number: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)

    # ── Customer info ─────────────────────────────────────────────────────────
    customer_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True,
        comment="NULL for guest orders"
    )
    guest_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    guest_phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # ── Address snapshot (embedded JSON at order time) ────────────────────────
    shipping_address: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # {fullName, phone, addressLine, landmark, city, state, pincode, type}

    # ── Delivery / payment ────────────────────────────────────────────────────
    delivery_method: Mapped[str] = mapped_column(String(20), nullable=False, default="standard")
    payment_method: Mapped[str] = mapped_column(String(30), nullable=False)  # upi|card|netbanking|cod

    # ── Status ────────────────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="ORDER_CONFIRMED", index=True)
    payment_status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING")

    # ── Pricing totals (rupees) ───────────────────────────────────────────────
    subtotal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    product_discount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    coupon_discount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    shipping_fee: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cod_fee: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── Coupon ────────────────────────────────────────────────────────────────
    coupon_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    coupon_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    # ── Customer note ─────────────────────────────────────────────────────────
    customer_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Inventory reservation (consumed on placement) ─────────────────────────
    inventory_reservation_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    # ── Fulfillment ───────────────────────────────────────────────────────────
    fulfillment_location_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    fulfillment_handler_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    tracking_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    carrier: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    estimated_delivery: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    dispatched_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Internal notes (admin only) ───────────────────────────────────────────
    internal_notes: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, default=list)
    # [{ id, authorId, authorName, note, createdAt }]

    # ── Timeline (append-only event log) ─────────────────────────────────────
    timeline: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, default=list)
    # [{ event, at, actorId?, actorName?, note? }]

    # ── Invoice stub ──────────────────────────────────────────────────────────
    invoice_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    invoice_issued_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Cancellation ──────────────────────────────────────────────────────────
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancellation_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cancelled_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    # ── Relationships ─────────────────────────────────────────────────────────
    items: Mapped[List["OrderItemModel"]] = relationship(
        "OrderItemModel", back_populates="order", cascade="all, delete-orphan"
    )
    status_history: Mapped[List["OrderStatusHistoryModel"]] = relationship(
        "OrderStatusHistoryModel", back_populates="order", cascade="all, delete-orphan"
    )
    returns: Mapped[List["ReturnOrderModel"]] = relationship(
        "ReturnOrderModel", back_populates="order", cascade="all, delete-orphan"
    )
