"""
CartItemModel — a single line in the server-side cart.

Line identity: (cart_id, product_id, color, size).
Duplicate triples are merged (quantities added) by the service layer.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class CartItemModel(Base):
    """One product line inside a cart."""

    __tablename__ = "commerce_cart_item"
    __table_args__ = (
        UniqueConstraint("cart_id", "product_id", "color", "size", name="uq_cart_item_line"),
    )

    # ── Parent cart ───────────────────────────────────────────────────────────
    cart_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("commerce_cart.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Product reference (id or slug at add-time) ────────────────────────────
    product_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)

    # ── Variant selectors (nullable — some products have no variants) ─────────
    color: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    size: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # ── Quantity (service clamps to available stock on every read) ────────────
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # ── Timestamp the line was first added (preserved on quantity update) ─────
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    cart: Mapped["CartModel"] = relationship("CartModel", back_populates="items")
