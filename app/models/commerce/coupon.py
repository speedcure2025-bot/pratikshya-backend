"""
CouponModel — promotional offer / discount code.

Validation rules mirror offerRepository.validateOffer() from the frontend:
  - minimum order value
  - active date window
  - usage limits (global + per-customer)
  - customer / product / category / collection eligibility
  - exclusion lists
  - stackability flag

discount_type: "percentage" | "fixed" | "free_shipping"
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CouponModel(Base):
    """A promotional coupon / offer code."""

    __tablename__ = "commerce_coupon"

    # ── Identity ──────────────────────────────────────────────────────────────
    code: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Discount ──────────────────────────────────────────────────────────────
    discount_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="percentage"
    )  # percentage | fixed | free_shipping
    discount_value: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # ── Conditions ───────────────────────────────────────────────────────────
    minimum_order_value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    starts_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Usage limits ─────────────────────────────────────────────────────────
    usage_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    per_customer_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # ── Eligibility (stored as JSON arrays) ──────────────────────────────────
    eligible_customer_ids: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    eligible_product_ids: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    eligible_category_ids: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    eligible_collection_ids: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    excluded_product_ids: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    excluded_category_ids: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    # ── Behaviour ────────────────────────────────────────────────────────────
    is_stackable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # ── Audit ─────────────────────────────────────────────────────────────────
    created_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
