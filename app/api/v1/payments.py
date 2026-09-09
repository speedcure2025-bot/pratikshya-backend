"""
Payments — API router (Razorpay integration).

URL mapping (API_CONTRACT.md → implementation):

  POST /payments/session               ← create Razorpay order + session
  GET  /payments/session/{sessionId}   ← get session status
  POST /payments/session/{sessionId}/cancel  ← cancel active session
  POST /payments/verify                ← client-side HMAC verification
  POST /payments/webhook               ← Razorpay signed webhook events

Flow:
  1. Frontend calls POST /payments/session after order placement.
  2. Backend creates a Razorpay order, returns razorpay_order_id + key_id.
  3. Frontend opens razorpay-checkout.js with those values.
  4. On success, frontend calls POST /payments/verify with the 3-field callback.
  5. Backend verifies HMAC, updates order.payment_status = PAID.
  6. Razorpay also sends async POST /payments/webhook for server-side confirmation.

COD:
  • POST /payments/session with payment_method=cod returns immediately with
    a local session — no Razorpay API call is made.
  • The order payment_status stays PENDING until delivery.

Security:
  • /payments/verify — HMAC-SHA256 of (razorpay_order_id + "|" + razorpay_payment_id)
    verified with RAZORPAY_KEY_SECRET.
  • /payments/webhook — X-Razorpay-Signature HMAC-SHA256 of raw body,
    verified with RAZORPAY_WEBHOOK_SECRET.
  • Raw bytes are read in the webhook endpoint BEFORE any JSON parsing to
    ensure the signature covers the exact bytes Razorpay signed.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Header, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_customer, get_db, get_optional_user
from app.models.auth.user import UserModel
from app.schemas.payments.payment import (
    CancelSessionRequest,
    CancelSessionResponse,
    CreatePaymentSessionRequest,
    GetSessionResponse,
    PaymentSessionData,
    VerifyPaymentRequest,
    VerifyPaymentResponse,
    WebhookAckResponse,
)
from app.services.payments.payment_service import PaymentService

router = APIRouter(prefix="/payments", tags=["Payments & Gateway"])


# ===========================================================================
# POST /payments/session — create Razorpay order + session
# ===========================================================================

@router.post(
    "/session",
    status_code=status.HTTP_201_CREATED,
    summary="Create a payment session (Razorpay order)",
    description=(
        "Creates a Razorpay order for online payments (UPI/card/netbanking) "
        "and returns the `razorpayOrderId` + `razorpayKeyId` needed to open "
        "the Razorpay checkout modal on the frontend.  \n\n"
        "**Canonical flow (Phase 2):** the order is created first "
        "(`POST /orders` → `PENDING_PAYMENT`), then this session. The charge "
        "amount is the order's authoritative server-computed total — client "
        "drafts are rejected. **COD** orders do not use payment sessions.  \n\n"
        "**Ownership:** the caller must own the order (authenticated customer, "
        "or the order's own guest email via `guestEmail`).  \n\n"
        "**Idempotency:** pass `idempotencyKey` to safely retry; an existing "
        "active session for the order is resumed instead of duplicated.  \n\n"
        "**Auth:** Customer session or guest (guest email required for guest orders)."
    ),
)
async def create_payment_session(
    req: CreatePaymentSessionRequest,
    current_user: Optional[UserModel] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Response shape (snake_case — the frontend API layer normalises):
      { ok, session_id, status, razorpay_order_id, razorpay_key_id,
        amount_paise, currency, prefill }

    The order must already exist (pending order first). The amount is the
    order's authoritative server-computed total. COD is rejected here.
    """
    service = PaymentService(db)

    # Prefill comes only from the authenticated identity — never from the
    # request body (guest prefill is not trusted).
    customer_name: Optional[str] = None
    customer_email: Optional[str] = None
    customer_phone: Optional[str] = None

    if current_user:
        customer_name = current_user.full_name
        customer_email = current_user.email
        customer_phone = current_user.phone

    result = await service.create_session(
        order_id=req.order_id,
        payment_method=req.payment_method,
        order_draft=req.order_draft,
        idempotency_key=req.idempotency_key,
        customer_email=customer_email,
        customer_phone=customer_phone,
        customer_name=customer_name,
        owner_customer_id=current_user.id if current_user else None,
        owner_guest_email=req.guest_email,
    )

    return result


# ===========================================================================
# GET /payments/session/{sessionId} — get session status
# ===========================================================================

@router.get(
    "/session/{session_id}",
    response_model=GetSessionResponse,
    summary="Get payment session status",
    description=(
        "Returns the current status of a payment session.  \n\n"
        "Status lifecycle: `CREATED → PENDING → PAID | FAILED | CANCELLED | EXPIRED`  \n\n"
        "**Ownership:** the caller must own the session's order — authenticated "
        "customer, or the order's guest email via the `guestEmail` query parameter."
    ),
)
async def get_payment_session(
    session_id: str,
    guest_email: Optional[str] = Query(None, alias="guestEmail", max_length=255),
    current_user: Optional[UserModel] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    service = PaymentService(db)
    session = await service.get_session(
        session_id,
        owner_customer_id=current_user.id if current_user else None,
        owner_guest_email=guest_email,
    )

    session_data = PaymentSessionData(
        id=session.id,
        orderId=session.order_id,
        razorpayOrderId=session.razorpay_order_id,
        razorpayPaymentId=session.razorpay_payment_id,
        amountPaise=session.amount_paise,
        amountRupees=session.amount_paise / 100,
        currency=session.currency,
        paymentMethod=session.payment_method,
        status=session.status,
        paidAt=session.paid_at,
        cancelledAt=session.cancelled_at,
        expiresAt=session.expires_at,
        failureReason=session.failure_reason,
        failureCode=session.failure_code,
        createdAt=session.created_at,
        updatedAt=session.updated_at,
    )

    return GetSessionResponse(session=session_data)


# ===========================================================================
# POST /payments/session/{sessionId}/cancel — cancel active session
# ===========================================================================

@router.post(
    "/session/{session_id}/cancel",
    response_model=CancelSessionResponse,
    summary="Cancel an active payment session",
    description=(
        "Cancels a payment session that is in `CREATED` or `PENDING` status.  \n\n"
        "Sessions in terminal states (`PAID`, `FAILED`, `CANCELLED`, `EXPIRED`) "
        "cannot be cancelled.  \n\n"
        "**Ownership is required:** authenticated customer, or the order's "
        "guest email via `guestEmail` in the request body."
    ),
)
async def cancel_payment_session(
    session_id: str,
    req: CancelSessionRequest,
    current_user: Optional[UserModel] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    service = PaymentService(db)

    session = await service.cancel_session(
        session_id=session_id,
        reason=req.reason,
        owner_customer_id=current_user.id if current_user else None,
        owner_guest_email=req.guest_email,
    )

    return CancelSessionResponse(
        sessionId=session.id,
        status=session.status,
    )


# ===========================================================================
# POST /payments/verify — client-side HMAC signature verification
# ===========================================================================

@router.post(
    "/verify",
    response_model=VerifyPaymentResponse,
    summary="Verify Razorpay payment signature (client callback)",
    description=(
        "Called by the frontend immediately after `razorpay.open()` returns "
        "a successful payment callback.  \n\n"
        "**Security:** Recomputes HMAC-SHA256 of "
        "`razorpay_order_id + '|' + razorpay_payment_id` using `RAZORPAY_KEY_SECRET` "
        "and compares it (constant-time) against the provided `razorpay_signature`.  \n\n"
        "Additionally fetches the payment from Razorpay to cross-check the amount "
        "matches the order total.  \n\n"
        "On success: the session and order move to `PAID`, and the order "
        "transitions `PENDING_PAYMENT → PAYMENT_CONFIRMED → ORDER_CONFIRMED`.  \n"
        "On failure: returns HTTP 422 and marks the session as `FAILED`.  \n\n"
        "**Ownership:** the caller must own the order — authenticated customer, "
        "or the order's guest email via `guestEmail`. A client can never mark "
        "an order `PAID` without a valid signature + ownership.  \n\n"
        "**Auth:** Customer session or guest (guest email required for guest orders)."
    ),
)
async def verify_payment(
    req: VerifyPaymentRequest,
    current_user: Optional[UserModel] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    service = PaymentService(db)
    result = await service.verify_payment(
        razorpay_order_id=req.razorpay_order_id,
        razorpay_payment_id=req.razorpay_payment_id,
        razorpay_signature=req.razorpay_signature,
        owner_customer_id=current_user.id if current_user else None,
        owner_guest_email=req.guest_email,
    )

    return VerifyPaymentResponse(
        ok=result["ok"],
        message=result["message"],
        paymentStatus=result.get("payment_status"),
        orderId=result.get("order_id"),
        orderStatus=result.get("order_status"),
    )


# ===========================================================================
# POST /payments/webhook — Razorpay signed webhook events
# ===========================================================================

@router.post(
    "/webhook",
    response_model=WebhookAckResponse,
    summary="Razorpay webhook endpoint",
    description=(
        "Receives signed webhook events from Razorpay.  \n\n"
        "**Security:** The `X-Razorpay-Signature` header is verified as "
        "HMAC-SHA256 of the **raw request body** using `RAZORPAY_WEBHOOK_SECRET`. "
        "Requests with a missing or invalid signature are rejected with HTTP 403.  \n\n"
        "Supported events:  \n"
        "- `payment.captured` → session + order set to `PAID`  \n"
        "- `payment.failed` → session + order set to `FAILED`  \n"
        "- `order.paid` → idempotent confirmation  \n\n"
        "All handlers are idempotent — Razorpay may deliver events more than once.  \n\n"
        "**IMPORTANT:** Raw bytes are read BEFORE any JSON parsing to ensure "
        "the HMAC covers exactly the bytes that Razorpay signed."
    ),
    include_in_schema=True,
)
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: Optional[str] = Header(
        None,
        alias="X-Razorpay-Signature",
        description="HMAC-SHA256 signature of the raw request body, signed with RAZORPAY_WEBHOOK_SECRET",
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    SECURITY CRITICAL:
      - raw_body is read before any parsing — the HMAC must cover the exact
        bytes Razorpay signed.
      - If X-Razorpay-Signature is absent, we reject with 403 immediately
        (no service call, no DB write).
      - We return HTTP 200 for all successfully authenticated events,
        even unknown ones, so Razorpay does not retry unnecessarily.
    """
    from app.core.exceptions import ForbiddenException

    if not x_razorpay_signature:
        raise ForbiddenException(
            "Missing X-Razorpay-Signature header. "
            "This endpoint only accepts signed requests from Razorpay."
        )

    # Read raw bytes BEFORE any framework parsing
    raw_body: bytes = await request.body()

    service = PaymentService(db)
    result = await service.handle_webhook(
        raw_body=raw_body,
        signature=x_razorpay_signature,
    )

    return WebhookAckResponse(
        ok=result["ok"],
        message=result.get("message", "Webhook processed."),
    )
