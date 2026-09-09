"""
CartModel — server-side cart for authenticated customers.

One cart per customer. Guest carts are client-only (per spec).
The cart stores line references; product data is re-resolved on every read.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class CartModel(Base):
    """Server-side shopping cart belonging to a single customer."""

    __tablename__ = "commerce_cart"

    # ── Ownership ─────────────────────────────────────────────────────────────
    customer_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,   # one cart per customer
        index=True,
    )

    # ── Applied coupon (kept even after validation; cleared on order) ─────────
    coupon_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    coupon_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("commerce_coupon.id", ondelete="SET NULL"),
        nullable=True,
    )
    coupon_lapsed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # ── Session note (forwarded to order on checkout) ─────────────────────────
    customer_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Relationships ─────────────────────────────────────────────────────────
    items: Mapped[list["CartItemModel"]] = relationship(
        "CartItemModel",
        back_populates="cart",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
