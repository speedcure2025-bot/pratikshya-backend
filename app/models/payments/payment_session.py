"""
PaymentSessionModel — persists a Razorpay payment session linked to an order.

Lifecycle:
  CREATED   → session created, Razorpay order generated, awaiting payment
  PENDING   → payment initiated on the client, awaiting Razorpay callback
  PAID      → HMAC signature verified, payment captured successfully
  FAILED    → payment failed at Razorpay
  CANCELLED → session explicitly cancelled before payment attempt
  EXPIRED   → session aged out without a payment (no webhook received)

The `razorpay_order_id` is the ID returned by Razorpay's Create Order API.
The `razorpay_payment_id` and `razorpay_signature` are set on successful
callback and used for HMAC verification.

COD orders do NOT create a PaymentSession — they are handled entirely
within the Order model (payment_status = PENDING).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PaymentSessionModel(Base):
    """Razorpay payment session record — one per online payment attempt."""

    __tablename__ = "payment_sessions"

    # ── Linked order ──────────────────────────────────────────────────────────
    order_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("orders_order.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Razorpay IDs ──────────────────────────────────────────────────────────
    razorpay_order_id: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, unique=True, index=True,
        comment="Razorpay `order_id` returned by Create Order API"
    )
    razorpay_payment_id: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, index=True,
        comment="Razorpay `payment_id` received in payment callback"
    )
    razorpay_signature: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True,
        comment="HMAC-SHA256 signature for verification"
    )

    # ── Amount & currency (paise — Razorpay uses smallest unit) ─────────────
    amount_paise: Mapped[int] = mapped_column(
        Integer, nullable=False,
        comment="Amount in paise (₹1 = 100 paise)"
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="INR"
    )

    # ── Payment method (upi | card | netbanking | cod) ───────────────────────
    payment_method: Mapped[str] = mapped_column(String(30), nullable=False)

    # ── Session status ────────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="CREATED", index=True
    )
    # CREATED | PENDING | PAID | FAILED | CANCELLED | EXPIRED

    # ── Timestamps ────────────────────────────────────────────────────────────
    paid_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="When this session is considered expired (15 min default)"
    )

    # ── Failure info ──────────────────────────────────────────────────────────
    failure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    failure_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # ── Webhook event log (last received event type) ──────────────────────────
    last_webhook_event: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # ── Idempotency key (prevents duplicate session creation) ─────────────────
    idempotency_key: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, unique=True, index=True,
        comment="Client-supplied idempotency key to prevent duplicate sessions"
    )

    # ── Optional: receipt / notes sent to Razorpay ────────────────────────────
    razorpay_receipt: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    razorpay_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
