"""
CouponRedemptionModel — tracks per-customer coupon usage.

Used to enforce per_customer_limit during validation.
A redemption is recorded when an order is placed, NOT when the coupon is
applied to the cart. The cart application only validates eligibility.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CouponRedemptionModel(Base):
    """One redemption record: one customer × one coupon × one order."""

    __tablename__ = "commerce_coupon_redemption"

    coupon_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("commerce_coupon.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    coupon_code: Mapped[str] = mapped_column(String(100), nullable=False)
    discount_amount: Mapped[int] = mapped_column(nullable=False, default=0)
