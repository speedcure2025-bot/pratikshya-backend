"""
InstagramRewardModel — tracks the full lifecycle of an Instagram post reward.

Business rules enforced at the application layer (not in constraints):
  - customer_id + order_id must be verified to belong to each other before approval.
  - Only one APPROVED reward is allowed per order (unique_order_approved checked in service).
  - Status transitions: PENDING → APPROVED | REJECTED (terminal states).
  - coupon_id is set only on APPROVED; remains NULL while PENDING or REJECTED.
  - verified_by / verified_at are set only when status transitions to APPROVED or REJECTED.

Status values (stored as plain String — project convention):
  PENDING   — submitted, awaiting staff review.
  APPROVED  — verified; coupon generated and linked.
  REJECTED  — submission did not meet the criteria.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Valid status values — kept here as module constants so the service
# layer can import them without importing the full ORM model in tests.
INSTAGRAM_REWARD_STATUS_PENDING = "PENDING"
INSTAGRAM_REWARD_STATUS_APPROVED = "APPROVED"
INSTAGRAM_REWARD_STATUS_REJECTED = "REJECTED"

INSTAGRAM_REWARD_STATUSES = frozenset({
    INSTAGRAM_REWARD_STATUS_PENDING,
    INSTAGRAM_REWARD_STATUS_APPROVED,
    INSTAGRAM_REWARD_STATUS_REJECTED,
})


class InstagramRewardModel(Base):
    """One Instagram reward submission per customer per order."""

    __tablename__ = "commerce_instagram_reward"

    # ── Customer (who posted) ─────────────────────────────────────────────────
    customer_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="User id of the customer who made the purchase and posted on Instagram.",
    )

    # ── Order (the purchase they posted about) ────────────────────────────────
    order_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("orders_order.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Order id the Instagram post relates to. Must belong to customer_id.",
    )

    # ── Instagram identifiers ────────────────────────────────────────────────
    instagram_username: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Customer's Instagram handle (without the leading @).",
    )
    instagram_post_url: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        comment="Direct URL to the Instagram post — optional but strongly recommended for audit.",
    )

    # ── Status ────────────────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=INSTAGRAM_REWARD_STATUS_PENDING,
        index=True,
        comment="PENDING | APPROVED | REJECTED",
    )

    # ── Verification audit ────────────────────────────────────────────────────
    verified_by: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="User id of the staff member who approved or rejected the submission.",
    )
    verified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="UTC timestamp when the submission was approved or rejected.",
    )
    rejection_reason: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Staff-entered reason shown when status = REJECTED.",
    )

    # ── Generated coupon ──────────────────────────────────────────────────────
    coupon_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("commerce_coupon.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="FK to the one-time personal coupon generated on approval. NULL while PENDING/REJECTED.",
    )
    coupon_code: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="Denormalised coupon code for display without a JOIN.",
    )
