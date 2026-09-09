"""
Pydantic schemas for the Payments API section.

Follows the frontend API_CONTRACT.md contract:
  POST   /payments/session           — create Razorpay order + session
  GET    /payments/session/{id}      — get session status
  POST   /payments/session/{id}/cancel  — cancel an active session
  POST   /payments/verify            — client-side HMAC verification
  POST   /payments/webhook           — Razorpay signed webhook events

All money is in **rupees** on the API surface.
Razorpay uses **paise** internally (₹1 = 100 paise) — conversion happens
inside the service layer, never on the schema layer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class CreatePaymentSessionRequest(BaseModel):
    """
    POST /payments/session

    Canonical lifecycle (Phase 2): the order is ALWAYS created first
    (POST /orders → pending order), then the payment session is created
    against that order. The charge amount is the order's authoritative
    server-computed total — a client-supplied draft amount is NOT trusted
    (the `order_draft` field is retained for backwards compatibility only
    and is rejected by the service).

    `order_id` is required.
    """
    order_id: Optional[str] = Field(
        None,
        description="ID of the pending order created at checkout (required)",
    )
    order_draft: Optional[Dict[str, Any]] = Field(
        None,
        description="Deprecated: pre-order drafts are no longer accepted. The order must exist first.",
    )
    payment_method: str = Field(
        ...,
        description="upi | card | netbanking (cod is rejected — COD orders do not use payment sessions)",
        examples=["upi"],
    )
    # Optional scenario hint (kept for staging/test environments only)
    demo_scenario: Optional[str] = Field(
        None,
        alias="demoScenario",
        description="Test scenario: success | failure | cancelled | pending",
    )
    idempotency_key: Optional[str] = Field(
        None,
        alias="idempotencyKey",
        description="Client-supplied key to prevent duplicate sessions",
    )
    # Guest checkout: identifies the guest who owns the order (server
    # compares it with the order's guest email — never trusted on its own).
    guest_email: Optional[str] = Field(
        None,
        alias="guestEmail",
        max_length=255,
        description="Guest order owner email (required for guest-owned orders)",
    )

    @field_validator("payment_method")
    @classmethod
    def validate_payment_method(cls, v: str) -> str:
        allowed = {"upi", "card", "netbanking", "cod"}
        if v not in allowed:
            raise ValueError(f"payment_method must be one of {allowed}")
        return v

    @field_validator("guest_email")
    @classmethod
    def normalize_guest_email(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        value = v.strip().lower()
        return value or None

    model_config = {"populate_by_name": True}


class VerifyPaymentRequest(BaseModel):
    """
    POST /payments/verify

    The frontend sends these three values after a successful Razorpay.open()
    callback so the backend can recompute and verify the HMAC-SHA256 signature.

    The signature is the ONLY trust anchor — a client can never mark an
    order PAID by sending a status flag. `guest_email` (guest checkout) is
    checked against the order's own guest email so a caller cannot verify a
    payment for an order they do not own.
    """
    razorpay_order_id: str = Field(..., alias="razorpayOrderId")
    razorpay_payment_id: str = Field(..., alias="razorpayPaymentId")
    razorpay_signature: str = Field(..., alias="razorpaySignature")
    guest_email: Optional[str] = Field(
        None,
        alias="guestEmail",
        max_length=255,
        description="Guest order owner email (required for guest-owned orders)",
    )

    model_config = {"populate_by_name": True}


class CancelSessionRequest(BaseModel):
    """
    POST /payments/session/{sessionId}/cancel
    """
    reason: Optional[str] = Field(None, description="Reason for cancellation")
    guest_email: Optional[str] = Field(
        None,
        alias="guestEmail",
        max_length=255,
        description="Guest order owner email (required for guest-owned orders)",
    )


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class PaymentSessionData(BaseModel):
    """Serialisable representation of a PaymentSessionModel."""
    id: str
    order_id: str = Field(..., alias="orderId")
    razorpay_order_id: Optional[str] = Field(None, alias="razorpayOrderId")
    razorpay_payment_id: Optional[str] = Field(None, alias="razorpayPaymentId")
    amount_paise: int = Field(..., alias="amountPaise")
    amount_rupees: float = Field(..., alias="amountRupees")
    currency: str
    payment_method: str = Field(..., alias="paymentMethod")
    status: str
    paid_at: Optional[datetime] = Field(None, alias="paidAt")
    cancelled_at: Optional[datetime] = Field(None, alias="cancelledAt")
    expires_at: Optional[datetime] = Field(None, alias="expiresAt")
    failure_reason: Optional[str] = Field(None, alias="failureReason")
    failure_code: Optional[str] = Field(None, alias="failureCode")
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")

    model_config = {"from_attributes": True, "populate_by_name": True}


class CreateSessionResponse(BaseModel):
    """
    Response for POST /payments/session

    Contains everything the Razorpay SDK (razorpay-checkout.js) needs
    to open the payment modal on the frontend.
    """
    ok: bool = True
    session_id: str = Field(..., alias="sessionId")
    status: str
    razorpay_order_id: str = Field(..., alias="razorpayOrderId")
    razorpay_key_id: str = Field(..., alias="razorpayKeyId")
    amount_paise: int = Field(..., alias="amountPaise")
    currency: str = "INR"
    # Optional prefill hints for the Razorpay modal
    prefill: Optional[Dict[str, str]] = None

    model_config = {"populate_by_name": True}


class CODSessionResponse(BaseModel):
    """Response for a COD order — no Razorpay order is created."""
    ok: bool = True
    session_id: str = Field(..., alias="sessionId")
    status: str = "CREATED"
    payment_method: str = Field("cod", alias="paymentMethod")
    message: str = "Cash on delivery — no online payment required."

    model_config = {"populate_by_name": True}


class GetSessionResponse(BaseModel):
    ok: bool = True
    session: PaymentSessionData

    model_config = {"populate_by_name": True}


class VerifyPaymentResponse(BaseModel):
    ok: bool
    message: str
    payment_status: Optional[str] = Field(None, alias="paymentStatus")
    order_id: Optional[str] = Field(None, alias="orderId")
    order_status: Optional[str] = Field(None, alias="orderStatus")

    model_config = {"populate_by_name": True}


class CancelSessionResponse(BaseModel):
    ok: bool = True
    session_id: str = Field(..., alias="sessionId")
    status: str = "CANCELLED"

    model_config = {"populate_by_name": True}


class WebhookAckResponse(BaseModel):
    """Minimal 200 OK response for Razorpay webhooks."""
    ok: bool = True
    message: str = "Webhook processed."
