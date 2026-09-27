"""
PaymentService — production Razorpay integration for Pratikshya Fashon.

Flow for ONLINE payments (upi | card | netbanking):
─────────────────────────────────────────────────────────────────────────────
  1. POST /payments/session
       • Validate the order exists (or draft amount).
       • Create a Razorpay Order via the REST API.
       • Persist a PaymentSessionModel with status=CREATED.
       • Return razorpay_order_id + razorpay_key_id to the frontend.

  2. Frontend opens razorpay-checkout.js with the returned values.
     User completes payment on Razorpay's hosted UI.

  3. Razorpay sends callback data to the frontend:
       { razorpay_order_id, razorpay_payment_id, razorpay_signature }

  4. POST /payments/verify  (called by frontend immediately after success)
       • Recompute HMAC-SHA256(razorpay_order_id + "|" + razorpay_payment_id)
         using RAZORPAY_KEY_SECRET.
       • Compare against razorpay_signature (constant-time).
       • On match → update session to PAID, update order.payment_status = PAID.
       • On mismatch → update session to FAILED, raise 400.

  5. POST /payments/webhook  (async, sent directly by Razorpay to the server)
       • Verify X-Razorpay-Signature header (HMAC of raw body with WEBHOOK_SECRET).
       • Handle event types: payment.captured, payment.failed, order.paid.
       • Update session + order payment status idempotently.

Flow for COD:
─────────────────────────────────────────────────────────────────────────────
  • POST /payments/session with payment_method=cod creates a minimal session
    (no Razorpay API call) with status=CREATED and returns a synthetic response.
  • Order payment_status stays PENDING until delivery.

Security guarantees:
─────────────────────────────────────────────────────────────────────────────
  ✓ HMAC-SHA256 signature verification — prevents payment forging.
  ✓ Webhook signature verification — prevents spoofed webhook events.
  ✓ Amount cross-check — verifies Razorpay amount matches our DB record.
  ✓ Constant-time comparison (hmac.compare_digest) — prevents timing attacks.
  ✓ Idempotency key — prevents duplicate session creation.
  ✓ Status guard — only CREATED/PENDING sessions can be moved to PAID/FAILED.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import razorpay
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import (
    BusinessLogicException,
    ConflictException,
    ForbiddenException,
    NotFoundException,
)
from app.core.logging import get_logger
from app.models.enums import (
    OrderPaymentStatus,
    PaymentSessionStatus,
    validate_order_payment_transition,
    validate_payment_session_transition,
)
from app.models.orders.order import OrderModel
from app.models.payments.payment_session import PaymentSessionModel

logger = get_logger("app.payments.payment_service")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SESSION_EXPIRY_MINUTES = 15
"""Payment sessions expire after 15 minutes if no payment is captured."""

PAYMENT_METHODS_REQUIRING_RAZORPAY = {"upi", "card", "netbanking"}
"""Methods that require a real Razorpay Order to be created."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _to_paise(rupees: int) -> int:
    """Convert whole rupees to paise (smallest Razorpay unit)."""
    return rupees * 100


def _to_rupees(paise: int) -> float:
    return paise / 100


def _build_razorpay_client() -> razorpay.Client:
    """
    Construct an authenticated Razorpay client.

    Raises RuntimeError if credentials are not configured.
    This is intentional — we want a clear startup-time failure, not a silent
    production error when keys are missing.
    """
    key_id = settings.RAZORPAY_KEY_ID
    key_secret = settings.RAZORPAY_KEY_SECRET

    if not key_id or not key_secret or key_id.startswith("your-"):
        raise RuntimeError(
            "RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET must be set in environment variables."
        )

    return razorpay.Client(auth=(key_id, key_secret))


def _verify_payment_signature(
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str,
) -> bool:
    """
    Verify Razorpay payment callback HMAC-SHA256 signature.

    Algorithm (per Razorpay docs):
        message  = razorpay_order_id + "|" + razorpay_payment_id
        expected = HMAC-SHA256(message, key=RAZORPAY_KEY_SECRET)
        compare  = hmac.compare_digest(expected_hex, razorpay_signature)

    Uses hmac.compare_digest for constant-time comparison to prevent
    timing-based oracle attacks.
    """
    if not razorpay_order_id or not razorpay_payment_id or not razorpay_signature:
        return False

    key_secret = settings.RAZORPAY_KEY_SECRET
    if not key_secret:
        raise RuntimeError("RAZORPAY_KEY_SECRET is not configured.")

    message = f"{razorpay_order_id}|{razorpay_payment_id}".encode("utf-8")
    expected = hmac.new(
        key_secret.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, razorpay_signature)


def _verify_webhook_signature(raw_body: bytes, signature: str) -> bool:
    """
    Verify Razorpay webhook HMAC-SHA256 signature.

    Algorithm (per Razorpay docs):
        expected = HMAC-SHA256(raw_body, key=RAZORPAY_WEBHOOK_SECRET)
        compare  = hmac.compare_digest(expected_hex, X-Razorpay-Signature)

    IMPORTANT: `raw_body` must be the exact bytes received — never decode/re-encode.
    """
    webhook_secret = settings.RAZORPAY_WEBHOOK_SECRET
    if not webhook_secret:
        raise RuntimeError("RAZORPAY_WEBHOOK_SECRET is not configured.")

    expected = hmac.new(
        webhook_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# PaymentService
# ---------------------------------------------------------------------------

class PaymentService:
    """Business logic for the Payments section."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Load helpers ──────────────────────────────────────────────────────────

    async def _load_session(
        self, session_id: str, lock: bool = False
    ) -> PaymentSessionModel:
        stmt = select(PaymentSessionModel).where(PaymentSessionModel.id == session_id)
        if lock:
            stmt = stmt.with_for_update()
        result = await self.db.execute(stmt)
        session = result.scalars().first()
        if not session:
            raise NotFoundException(f"Payment session '{session_id}' not found.")
        return session

    async def _load_session_by_razorpay_order(
        self, razorpay_order_id: str, lock: bool = False
    ) -> PaymentSessionModel:
        stmt = select(PaymentSessionModel).where(
            PaymentSessionModel.razorpay_order_id == razorpay_order_id
        )
        if lock:
            stmt = stmt.with_for_update()
        result = await self.db.execute(stmt)
        session = result.scalars().first()
        if not session:
            raise NotFoundException(
                f"No payment session found for Razorpay order '{razorpay_order_id}'."
            )
        return session

    async def _load_order(self, order_id: str, lock: bool = False) -> OrderModel:
        stmt = select(OrderModel).where(OrderModel.id == order_id)
        if lock:
            stmt = stmt.with_for_update()
        result = await self.db.execute(stmt)
        order = result.scalars().first()
        if not order:
            raise NotFoundException(f"Order '{order_id}' not found.")
        return order

    # ── Payment Recovery & Reconciliation (Phase 8) ───────────────────────────

    async def _fetch_razorpay_order_payments(self, razorpay_order_id: str) -> list:
        """
        Fetch all payments associated with a Razorpay order.

        Used for recovery: when the frontend lost connection after bank approval
        and we never received the callback, we query Razorpay directly to check
        whether any payment was captured.

        Returns a list of payment entity dicts, or [] on any API error.
        """
        try:
            client = _build_razorpay_client()
            loop = asyncio.get_running_loop()
            data = await loop.run_in_executor(
                None, lambda: client.order.payments(razorpay_order_id)
            )
            # API returns {"count": N, "items": [...]}
            return data.get("items", [])
        except Exception as exc:
            logger.warning(
                "Razorpay order.payments fetch failed rzp_order_id=%s error=%s",
                razorpay_order_id, exc,
            )
            return []

    async def reconcile_session(
        self,
        session_id: str,
        owner_customer_id: Optional[str] = None,
        owner_guest_email: Optional[str] = None,
    ) -> dict:
        """
        GET /payments/session/{sessionId}/reconcile — network failure recovery.

        The CRITICAL SCENARIO this solves
        ──────────────────────────────────
        Customer pays successfully at Razorpay → bank approves → money deducted.
        Then BEFORE the frontend callback reaches our server:
          • browser closes
          • internet disconnects
          • frontend crashes
          • server temporarily fails

        The webhook will eventually recover the session, but the frontend
        re-connecting immediately may need an answer NOW (before the webhook
        arrives). This endpoint:

          1. Returns the current DB status immediately if it is already terminal
             (PAID / FAILED / CANCELLED / REFUNDED / PARTIALLY_REFUNDED).
             → No Razorpay API call needed.

          2. For non-terminal sessions (CREATED / PENDING / EXPIRED) that have a
             known `razorpay_order_id`, calls Razorpay's "fetch payments for order"
             API and checks whether any payment is in `captured` state.

          3. If captured → verify amount → apply the same _confirm_order_paid
             logic as the webhook handler → session = PAID, order = PAID.
             → Customer is NOT charged again.

          4. If failed / no payment → status unchanged; frontend knows to offer a
             retry (new session, same order).

        Safety guarantees:
          ✓ Never creates a new Razorpay order.
          ✓ Never creates a duplicate internal order.
          ✓ Never deducts inventory a second time.
          ✓ Idempotent — calling this endpoint N times is safe.
          ✓ Ownership enforced — caller must own the order.
          ✓ Pessimistic lock on session and order during update.
        """
        session = await self._load_session(session_id, lock=True)
        order = await self._load_order(session.order_id, lock=True)
        await self._assert_order_access(order, owner_customer_id, owner_guest_email)

        # ── 1. Already terminal — return immediately, no Razorpay call ─────────
        TERMINAL = ("PAID", "FAILED", "CANCELLED", "REFUNDED", "PARTIALLY_REFUNDED", "EXPIRED")
        if session.status in TERMINAL:
            return {
                "ok": True,
                "reconciled": False,
                "session_status": session.status,
                "order_payment_status": order.payment_status,
                "message": f"Session already in terminal status '{session.status}'. No reconciliation needed.",
            }

        # ── 2. No Razorpay order ID — cannot query Razorpay ───────────────────
        if not session.razorpay_order_id:
            return {
                "ok": True,
                "reconciled": False,
                "session_status": session.status,
                "order_payment_status": order.payment_status,
                "message": "No Razorpay order associated with this session.",
            }

        # ── 3. Query Razorpay for real payment state ──────────────────────────
        logger.info(
            "Reconciling session session_id=%s internal_order_id=%s razorpay_order_id=%s event_type=reconcile_start",
            session_id, session.order_id, session.razorpay_order_id,
        )
        payments = await self._fetch_razorpay_order_payments(session.razorpay_order_id)

        # Find the first captured payment for this order
        captured_payment = next(
            (p for p in payments if p.get("status") == "captured"),
            None,
        )

        if captured_payment:
            # ── 4. Payment captured — recover to PAID ─────────────────────────
            razorpay_payment_id: str = captured_payment.get("id", "")
            amount_paise: int = int(captured_payment.get("amount", 0))

            # Amount guard — must match what we expect
            if amount_paise != session.amount_paise:
                logger.error(
                    "Reconcile amount mismatch session_id=%s internal_order_id=%s expected_amount=%s actual_amount=%s payment_status=%s failure_reason=%s event_type=amount_mismatch",
                    session_id, session.order_id, session.amount_paise, amount_paise, session.status, "Amount mismatch between DB session and Razorpay payment",
                )
                # Do NOT mark FAILED based on reconcile alone — the webhook
                # will be the authoritative source for failures.
                return {
                    "ok": False,
                    "reconciled": False,
                    "session_status": session.status,
                    "order_payment_status": order.payment_status,
                    "message": (
                        f"Razorpay amount mismatch: expected {session.amount_paise} paise, "
                        f"got {amount_paise} paise. Contact support."
                    ),
                }

            now = _now_utc()
            validate_payment_session_transition(session.status, "PAID", has_verified_evidence=True)
            session.status = "PAID"
            session.razorpay_payment_id = razorpay_payment_id
            session.paid_at = now
            session.last_webhook_event = "reconcile:captured"
            await self.db.flush()

            await self._confirm_order_paid(
                order,
                now=now,
                note=f"reconcile:captured / razorpay_payment_id={razorpay_payment_id}",
            )

            logger.info(
                "Reconciliation recovered payment session_id=%s internal_order_id=%s razorpay_payment_id=%s payment_status=PAID order_status=%s event_type=reconcile_recovered",
                session_id, session.order_id, razorpay_payment_id, order.status,
            )
            return {
                "ok": True,
                "reconciled": True,
                "session_status": "PAID",
                "order_payment_status": "PAID",
                "message": "Payment reconciled successfully. Order is confirmed.",
            }

        # ── 5. Check for an explicitly failed payment ─────────────────────────
        failed_payment = next(
            (p for p in payments if p.get("status") == "failed"),
            None,
        )
        if failed_payment and session.status in ("CREATED", "PENDING"):
            # Only log — do NOT auto-mark FAILED; the authoritative failure
            # signal should come from the webhook. Return status-quo so the
            # frontend can decide whether to retry or wait for the webhook.
            logger.info(
                "Reconcile found failed payment session_id=%s internal_order_id=%s razorpay_payment_id=%s failure_reason=%s event_type=reconcile_found_failed",
                session_id, session.order_id, failed_payment.get("id"), failed_payment.get("error_description", "Payment failed at Razorpay"),
            )

        # ── 6. No conclusive payment found — status unchanged ─────────────────
        return {
            "ok": True,
            "reconciled": False,
            "session_status": session.status,
            "order_payment_status": order.payment_status,
            "message": (
                "No captured payment found at Razorpay yet. "
                "The webhook will update the status once payment is confirmed."
            ),
        }

    # ── Create payment session ─────────────────────────────────────────────────

    async def create_session(
        self,
        order_id: Optional[str],
        payment_method: str,
        order_draft: Optional[dict] = None,
        idempotency_key: Optional[str] = None,
        customer_email: Optional[str] = None,
        customer_phone: Optional[str] = None,
        customer_name: Optional[str] = None,
        owner_customer_id: Optional[str] = None,
        owner_guest_email: Optional[str] = None,
    ) -> dict:
        """
        POST /payments/session — canonical (Phase 2) flow.

        The order ALWAYS exists first (POST /orders created a pending
        order). The charge amount is the order's authoritative,
        server-computed `total` — client-supplied draft amounts are never
        trusted.

        Steps (upi/card/netbanking):
          1. Require `order_id`; reject COD and draft-only requests.
          2. Verify the caller owns the order (customer identity or the
             order's own guest email).
          3. Resume an existing active session for the order (retries must
             not create duplicate sessions / Razorpay orders).
          4. Call Razorpay Create Order API with the authoritative amount.
          5. Persist PaymentSessionModel (unique idempotency key).
          6. Return Razorpay order details (snake_case — the frontend API
             layer normalises to camelCase).
        """
        if order_draft is not None and not order_id:
            raise BusinessLogicException(
                "A payment session requires an existing order: create the order "
                "first (POST /orders), then create the payment session with its "
                "order id. Draft amounts are not trusted."
            )
        if not order_id:
            raise BusinessLogicException(
                "'order_id' is required — the order must be created before its payment session."
            )
        if payment_method == "cod":
            raise BusinessLogicException(
                "COD orders do not use a payment session — the order lifecycle "
                "handles cash on delivery (order stays payment_status=PENDING "
                "until delivery)."
            )

        # ── Idempotency: return existing session if key matches ────────────────
        if idempotency_key:
            existing_stmt = select(PaymentSessionModel).where(
                PaymentSessionModel.idempotency_key == idempotency_key
            )
            existing_result = await self.db.execute(existing_stmt)
            existing = existing_result.scalars().first()
            if existing:
                return self._build_session_response(existing, prefill=None)

        # ── Load and guard the order ───────────────────────────────────────────
        order = await self._load_order(order_id)

        if order.status == "CANCELLED":
            raise BusinessLogicException(
                "Cannot create a payment session for a cancelled order."
            )
        if order.payment_status in ("PAID", "AUTHORIZED"):
            raise ConflictException(
                "This order has already been paid. No new payment session can be created."
            )

        # Ownership — the caller must own this order (never trust the id alone).
        await self._assert_order_access(order, owner_customer_id, owner_guest_email)

        # ── Resume an active session instead of creating a duplicate ───────────
        active_stmt = select(PaymentSessionModel).where(
            PaymentSessionModel.order_id == order.id,
            PaymentSessionModel.status.in_(["CREATED", "PENDING"]),
        )
        active_result = await self.db.execute(active_stmt)
        active_session = active_result.scalars().first()
        if active_session is not None:
            return self._build_session_response(active_session, prefill=None)

        # ── Authoritative amount from the order ────────────────────────────────
        amount_rupees = int(order.total or 0)
        if amount_rupees <= 0:
            raise BusinessLogicException("Payment amount must be greater than zero.")

        # ── Online payment: call Razorpay Create Order API ────────────────────
        amount_paise = _to_paise(amount_rupees)
        receipt = f"PF-{order.id[:8].upper()}"

        razorpay_order_data = await self._create_razorpay_order(
            amount_paise=amount_paise,
            receipt=receipt,
            notes={
                "order_id": order.id,
                "platform": "pratikshya_fashon",
            },
        )

        razorpay_order_id: str = razorpay_order_data["id"]

        # ── Persist session ────────────────────────────────────────────────────
        session = PaymentSessionModel(
            id=_new_uuid(),
            order_id=order.id,
            razorpay_order_id=razorpay_order_id,
            amount_paise=amount_paise,
            currency="INR",
            payment_method=payment_method,
            status="CREATED",
            expires_at=_now_utc() + timedelta(minutes=SESSION_EXPIRY_MINUTES),
            razorpay_receipt=receipt,
            idempotency_key=idempotency_key,
        )
        self.db.add(session)
        await self.db.flush()

        logger.info(
            "Payment session created session_id=%s internal_order_id=%s razorpay_order_id=%s amount_paise=%s payment_method=%s payment_status=%s event_type=session_created",
            session.id, order.id, razorpay_order_id, amount_paise, payment_method, session.status,
        )

        # ── Build prefill for Razorpay modal ───────────────────────────────────
        prefill: dict = {}
        if customer_name:
            prefill["name"] = customer_name
        if customer_email:
            prefill["email"] = customer_email
        if customer_phone:
            prefill["contact"] = customer_phone

        return {
            "ok": True,
            "session_id": session.id,
            "status": session.status,
            "razorpay_order_id": razorpay_order_id,
            "razorpay_key_id": settings.RAZORPAY_KEY_ID,
            "amount_paise": amount_paise,
            "currency": "INR",
            "prefill": prefill or None,
        }

    async def _assert_order_access(
        self,
        order: OrderModel,
        owner_customer_id: Optional[str],
        owner_guest_email: Optional[str],
    ) -> None:
        """
        Payment-session access control (Phase 2 trust model).

        - Customer-owned order: only that customer (authenticated) may act.
        - Guest-owned order: only a caller presenting the order's own guest
          email may act (an authenticated user cannot act on a guest order
          until they claim it via the verified-email claim flow).
        """
        if order.customer_id is not None:
            if owner_customer_id and order.customer_id == owner_customer_id:
                return
            raise ForbiddenException(
                "You do not have access to this order's payment session."
            )
        # Guest order
        if owner_customer_id:
            raise ForbiddenException(
                "You do not have access to this guest order's payment session."
            )
        guest = (owner_guest_email or "").strip().lower()
        if not guest or (order.guest_email or "").lower() != guest:
            raise ForbiddenException(
                "The provided guest email does not match this order."
            )

    async def _create_razorpay_order(
        self,
        amount_paise: int,
        receipt: str,
        notes: Optional[dict] = None,
    ) -> dict:
        """
        Call Razorpay Create Order API via thread executor (non-blocking).

        The razorpay Python SDK is synchronous — we offload the network call to an
        executor thread to avoid blocking FastAPI's event loop.
        """
        client = _build_razorpay_client()

        order_data = {
            "amount": amount_paise,
            "currency": "INR",
            "receipt": receipt,
            "notes": notes or {},
            "payment_capture": 1,  # auto-capture on successful payment
        }

        try:
            loop = asyncio.get_running_loop()
            razorpay_order = await loop.run_in_executor(
                None, lambda: client.order.create(data=order_data)
            )
        except (TimeoutError, asyncio.TimeoutError) as exc:
            logger.error(
                "Razorpay order creation timed out receipt=%s internal_order_id=%s amount_paise=%s failure_reason=%s event_type=payment_timeout",
                receipt, notes.get("order_id") if notes else None, amount_paise, exc,
            )
            raise BusinessLogicException(
                "Razorpay payment gateway connection timed out. Please try again."
            ) from exc
        except Exception as exc:
            err_msg = str(exc)
            if "timeout" in err_msg.lower():
                logger.error(
                    "Razorpay order creation timed out receipt=%s internal_order_id=%s amount_paise=%s failure_reason=%s event_type=payment_timeout",
                    receipt, notes.get("order_id") if notes else None, amount_paise, exc,
                )
                raise BusinessLogicException(
                    "Razorpay payment gateway connection timed out. Please try again."
                ) from exc
            logger.error(
                "Razorpay order creation failed receipt=%s internal_order_id=%s amount_paise=%s failure_reason=%s event_type=session_create_failed",
                receipt, notes.get("order_id") if notes else None, amount_paise, exc, exc_info=True,
            )
            raise BusinessLogicException(
                f"Failed to create Razorpay order: {exc}"
            ) from exc

        return razorpay_order

    def _build_session_response(
        self, session: PaymentSessionModel, prefill: Optional[dict] = None
    ) -> dict:
        """Build a consistent snake_case response dict from an existing session."""
        if session.payment_method == "cod":
            # Legacy rows from the pre-Phase-2 flow only.
            return {
                "ok": True,
                "session_id": session.id,
                "status": session.status,
                "payment_method": "cod",
                "message": "Cash on delivery — no online payment required.",
            }

        return {
            "ok": True,
            "session_id": session.id,
            "status": session.status,
            "razorpay_order_id": session.razorpay_order_id,
            "razorpay_key_id": settings.RAZORPAY_KEY_ID,
            "amount_paise": session.amount_paise,
            "currency": session.currency,
            "prefill": prefill,
        }

    # ── Get session ────────────────────────────────────────────────────────────

    async def get_session(
        self,
        session_id: str,
        owner_customer_id: Optional[str] = None,
        owner_guest_email: Optional[str] = None,
    ) -> PaymentSessionModel:
        """
        GET /payments/session/{sessionId}

        Ownership is enforced: customer-owned orders require the owning
        customer; guest-owned orders require the order's own guest email.
        """
        session = await self._load_session(session_id)
        order = await self._load_order(session.order_id)
        await self._assert_order_access(order, owner_customer_id, owner_guest_email)
        return session

    # ── Cancel session ─────────────────────────────────────────────────────────

    async def cancel_session(
        self,
        session_id: str,
        reason: Optional[str] = None,
        owner_customer_id: Optional[str] = None,
        owner_guest_email: Optional[str] = None,
    ) -> PaymentSessionModel:
        """
        POST /payments/session/{sessionId}/cancel

        Only CREATED or PENDING sessions can be cancelled.
        We do NOT call Razorpay's cancel API here because Razorpay orders
        cannot be cancelled programmatically — they simply expire (15 min TTL
        on Razorpay's side). We mark our session as CANCELLED locally.

        Ownership is REQUIRED (not optional): the caller must be the owning
        customer, or match the order's guest email.
        """
        session = await self._load_session(session_id)

        if session.status not in ("CREATED", "PENDING"):
            raise BusinessLogicException(
                f"Cannot cancel a session in status '{session.status}'. "
                "Only CREATED or PENDING sessions can be cancelled."
            )

        order = await self._load_order(session.order_id)
        await self._assert_order_access(order, owner_customer_id, owner_guest_email)

        validate_payment_session_transition(session.status, "CANCELLED")
        session.status = "CANCELLED"
        session.cancelled_at = _now_utc()
        session.failure_reason = reason or "Cancelled by user."
        await self.db.flush()

        logger.info(
            "Payment session cancelled session_id=%s internal_order_id=%s payment_status=CANCELLED failure_reason=%s event_type=session_cancelled",
            session.id, session.order_id, session.failure_reason,
        )

        return session

    # ── Verify payment (client-side callback) ──────────────────────────────────

    async def verify_payment(
        self,
        razorpay_order_id: str,
        razorpay_payment_id: str,
        razorpay_signature: str,
        owner_customer_id: Optional[str] = None,
        owner_guest_email: Optional[str] = None,
    ) -> dict:
        """
        POST /payments/verify

        Called by the frontend immediately after the Razorpay modal closes
        with a successful payment.

        Steps:
          1. Find the session by razorpay_order_id and its order.
          2. Ownership check (customer or matching guest email).
          3. Guard: cancelled orders can no longer be paid.
          4. Guard: session must be CREATED or PENDING (PAID → idempotent).
          5. Verify HMAC-SHA256 signature — reject + mark FAILED on mismatch.
          6. Cross-check amount against Razorpay when the provider is
             reachable (signature is the primary trust anchor).
          7. Session → PAID; order → PAID + PENDING_PAYMENT →
             PAYMENT_CONFIRMED → ORDER_CONFIRMED (canonical confirmation).

        SECURITY NOTE:
          The HMAC verification is the authoritative trust boundary.
          We do NOT trust the frontend's claim that payment succeeded —
          only a valid HMAC signed with our key_secret is accepted.
          A client can never mark an order PAID by sending a status flag.
        """
        session = await self._load_session_by_razorpay_order(razorpay_order_id, lock=True)
        order = await self._load_order(session.order_id, lock=True)
        await self._assert_order_access(order, owner_customer_id, owner_guest_email)

        # Guard: idempotent — already verified
        if session.status == "PAID":
            await self._confirm_order_paid(order, note="verification replay")
            logger.info(
                "Payment verification replay session_id=%s internal_order_id=%s razorpay_order_id=%s payment_status=PAID order_status=%s event_type=duplicate_verify",
                session.id, session.order_id, razorpay_order_id, order.status,
            )
            return {
                "ok": True,
                "message": "Payment already verified.",
                "payment_status": order.payment_status,
                "order_id": session.order_id,
                "order_status": order.status,
            }

        if session.status not in ("CREATED", "PENDING"):
            raise BusinessLogicException(
                f"Payment session is in status '{session.status}' — "
                "verification is only valid for CREATED or PENDING sessions."
            )

        # Guard: a cancelled order must never be charged.
        if order.status == "CANCELLED":
            session.status = "FAILED"
            session.failure_reason = "Order was cancelled before payment."
            session.failure_code = "ORDER_CANCELLED"
            await self.db.flush()
            logger.warning(
                "Cancelled order payment verify rejected session_id=%s internal_order_id=%s payment_status=FAILED failure_reason=%s event_type=payment_failed",
                session.id, session.order_id, session.failure_reason,
            )
            raise BusinessLogicException(
                "This order has been cancelled and can no longer be paid."
            )

        # ── HMAC signature verification ────────────────────────────────────────
        signature_valid = _verify_payment_signature(
            razorpay_order_id=razorpay_order_id,
            razorpay_payment_id=razorpay_payment_id,
            razorpay_signature=razorpay_signature,
        )

        if not signature_valid:
            # Mark session as failed to prevent re-attempts with a bad signature
            session.status = "FAILED"
            session.failure_reason = "HMAC signature verification failed."
            session.failure_code = "SIGNATURE_MISMATCH"
            await self.db.flush()

            logger.warning(
                "Payment verification signature mismatch session_id=%s internal_order_id=%s razorpay_order_id=%s razorpay_payment_id=%s payment_status=FAILED failure_reason=%s event_type=signature_verification_failed",
                session.id, session.order_id, razorpay_order_id, razorpay_payment_id, "HMAC signature verification failed",
            )

            raise BusinessLogicException(
                "Payment verification failed: invalid signature. "
                "This may indicate a tampered callback. Contact support if this persists."
            )

        # ── Amount cross-check via Razorpay fetch API ─────────────────────────
        # This is an extra security layer — we verify the amount Razorpay
        # recorded matches what we expect to charge.
        try:
            client = _build_razorpay_client()
            loop = asyncio.get_running_loop()
            payment_details = await loop.run_in_executor(
                None, lambda: client.payment.fetch(razorpay_payment_id)
            )
            fetched_rzp_order_id = payment_details.get("order_id")
            if fetched_rzp_order_id and fetched_rzp_order_id != razorpay_order_id:
                session.status = "FAILED"
                session.failure_reason = (
                    f"Payment order mismatch: payment belongs to '{fetched_rzp_order_id}', "
                    f"expected '{razorpay_order_id}'."
                )
                session.failure_code = "WRONG_RAZORPAY_ORDER"
                await self.db.flush()
                logger.warning(
                    "Payment order mismatch session_id=%s internal_order_id=%s razorpay_order_id=%s fetched_razorpay_order_id=%s payment_status=FAILED failure_reason=%s event_type=order_mismatch",
                    session.id, session.order_id, razorpay_order_id, fetched_rzp_order_id, session.failure_reason,
                )
                raise BusinessLogicException(
                    "Payment does not belong to this order. "
                    "Please contact support immediately."
                )

            razorpay_amount = int(payment_details.get("amount", 0))

            if razorpay_amount != session.amount_paise:
                session.status = "FAILED"
                session.failure_reason = (
                    f"Amount mismatch: expected {session.amount_paise} paise, "
                    f"Razorpay recorded {razorpay_amount} paise."
                )
                session.failure_code = "AMOUNT_MISMATCH"
                await self.db.flush()
                logger.warning(
                    "Payment amount mismatch session_id=%s internal_order_id=%s razorpay_order_id=%s razorpay_payment_id=%s expected_amount=%s actual_amount=%s payment_status=FAILED failure_reason=%s event_type=amount_mismatch",
                    session.id, session.order_id, razorpay_order_id, razorpay_payment_id, session.amount_paise, razorpay_amount, session.failure_reason,
                )
                raise BusinessLogicException(
                    "Payment amount does not match order total. "
                    "Please contact support immediately."
                )
        except BusinessLogicException:
            raise
        except Exception:
            # Razorpay fetch failed (e.g. provider not configured) — proceed
            # with signature verification alone (signature is the primary
            # trust anchor; the fetch is belt-and-suspenders).
            pass

        # ── All checks passed — mark PAID ─────────────────────────────────────
        now = _now_utc()
        validate_payment_session_transition(session.status, "PAID", has_verified_evidence=True)
        session.status = "PAID"
        session.razorpay_payment_id = razorpay_payment_id
        session.razorpay_signature = razorpay_signature
        session.paid_at = now
        await self.db.flush()

        logger.info(
            "Payment verified session_id=%s internal_order_id=%s razorpay_order_id=%s razorpay_payment_id=%s amount_paise=%s payment_status=PAID order_status=%s event_type=payment_verified",
            session.id, session.order_id, session.razorpay_order_id, razorpay_payment_id, session.amount_paise, order.status,
        )

        await self._confirm_order_paid(order, now=now, note=f"razorpay_payment_id={razorpay_payment_id}")

        return {
            "ok": True,
            "message": "Payment verified and captured successfully.",
            "payment_status": "PAID",
            "order_id": order.id,
            "order_status": order.status,
        }

    async def _confirm_order_paid(
        self,
        order: OrderModel,
        now: Optional[datetime] = None,
        note: Optional[str] = None,
    ) -> None:
        """
        Authoritative order confirmation after verified payment.

        - payment_status → PAID (guarded, idempotent)
        - PENDING_PAYMENT → PAYMENT_CONFIRMED → ORDER_CONFIRMED, each step
          written to status history + timeline.
        """
        from app.models.orders.order_status_history import OrderStatusHistoryModel

        now = now or _now_utc()
        if order.payment_status not in ("PAID", "AUTHORIZED"):
            validate_order_payment_transition(order.payment_status, "PAID", has_verified_evidence=True)
            order.payment_status = "PAID"

        if order.status == "PENDING_PAYMENT":
            for to_status in ("PAYMENT_CONFIRMED", "ORDER_CONFIRMED"):
                from_status = order.status
                order.status = to_status
                self.db.add(OrderStatusHistoryModel(
                    id=_new_uuid(),
                    order_id=order.id,
                    from_status=from_status,
                    to_status=to_status,
                    note=f"Payment verified — {note}" if note else "Payment verified.",
                ))
                timeline = list(order.timeline or [])
                timeline.append({
                    "event": f"STATUS_{to_status}",
                    "at": now.isoformat(),
                })
                order.timeline = timeline

        timeline = list(order.timeline or [])
        has_captured_event = any(isinstance(item, dict) and item.get("event") == "PAYMENT_CAPTURED" for item in timeline)
        if not has_captured_event:
            timeline.append({
                "event": "PAYMENT_CAPTURED",
                "at": now.isoformat(),
                "note": note,
            })
            order.timeline = timeline
        await self.db.flush()

    # ── Webhook handler ────────────────────────────────────────────────────────

    async def handle_webhook(self, raw_body: bytes, signature: str) -> dict:
        """
        POST /payments/webhook

        Razorpay sends signed events to this endpoint asynchronously.
        This is the server-side confirmation of payment, independent of the
        client-side verify flow (which depends on the user's browser completing).

        Security:
          1. Verify X-Razorpay-Signature HMAC using RAZORPAY_WEBHOOK_SECRET.
          2. Parse event payload.
          3. Dispatch to event-specific handler.

        Supported events:
          payment.captured  → session + order → PAID
          payment.failed    → session + order → FAILED
          order.paid        → idempotent confirmation (already handled by above)

        Idempotency:
          All handlers are idempotent — re-delivery of the same event is safe.
        """
        import json

        # ── Signature verification (primary security gate) ────────────────────
        if not _verify_webhook_signature(raw_body, signature):
            logger.warning(
                "Webhook signature verification failed failure_reason=%s event_type=webhook_signature_failed",
                "Invalid or missing X-Razorpay-Signature header",
            )
            raise ForbiddenException(
                "Webhook signature verification failed. "
                "The request does not appear to originate from Razorpay."
            )

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning(
                "Malformed webhook payload failure_reason=%s event_type=webhook_json_decode_failed",
                exc,
            )
            raise BusinessLogicException(f"Malformed webhook payload: {exc}") from exc

        event = payload.get("event")
        if not event:
            raise BusinessLogicException("Webhook payload is missing 'event' field.")

        logger.info("Webhook received event=%s event_type=webhook_received", event)

        # ── Event dispatch ────────────────────────────────────────────────────
        if event == "payment.captured":
            await self._on_payment_captured(payload)
        elif event == "payment.failed":
            await self._on_payment_failed(payload)
        elif event == "order.paid":
            await self._on_order_paid(payload)
        elif event == "refund.processed":
            await self._on_refund_processed(payload)
        else:
            # Unknown event — acknowledge without processing (do NOT return 4xx)
            logger.info("Unhandled webhook event received event=%s event_type=unhandled_webhook", event)

        return {"ok": True, "message": f"Event '{event}' processed."}

    async def _on_payment_captured(self, payload: dict) -> None:
        """Handle payment.captured webhook event."""
        entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
        razorpay_payment_id: str = entity.get("id", "")
        razorpay_order_id: str = entity.get("order_id", "")
        amount_paise: int = int(entity.get("amount", 0))

        if not razorpay_order_id:
            return  # Cannot correlate — skip

        try:
            session = await self._load_session_by_razorpay_order(razorpay_order_id, lock=True)
        except NotFoundException:
            return  # Session not found — already removed or test event

        # Idempotent: already PAID
        if session.status == "PAID":
            logger.info(
                "Duplicate webhook payment.captured ignored session_id=%s internal_order_id=%s razorpay_order_id=%s razorpay_payment_id=%s payment_status=PAID event_type=duplicate_webhook",
                session.id, session.order_id, razorpay_order_id, razorpay_payment_id,
            )
            return

        # Amount guard
        if amount_paise != session.amount_paise:
            validate_payment_session_transition(session.status, "FAILED")
            session.status = "FAILED"
            session.failure_reason = (
                f"Webhook amount mismatch: expected {session.amount_paise} paise, "
                f"received {amount_paise} paise."
            )
            session.failure_code = "AMOUNT_MISMATCH"
            session.last_webhook_event = "payment.captured"
            await self.db.flush()
            logger.warning(
                "Webhook payment.captured amount mismatch session_id=%s internal_order_id=%s razorpay_order_id=%s razorpay_payment_id=%s expected_amount=%s received_amount=%s payment_status=FAILED failure_reason=%s event_type=amount_mismatch",
                session.id, session.order_id, razorpay_order_id, razorpay_payment_id, session.amount_paise, amount_paise, session.failure_reason,
            )
            return

        now = _now_utc()
        validate_payment_session_transition(session.status, "PAID", has_verified_evidence=True)
        session.status = "PAID"
        session.razorpay_payment_id = razorpay_payment_id
        session.paid_at = now
        session.last_webhook_event = "payment.captured"
        await self.db.flush()

        logger.info(
            "Webhook payment.captured processed session_id=%s internal_order_id=%s razorpay_order_id=%s razorpay_payment_id=%s amount_paise=%s payment_status=PAID event_type=webhook_payment_captured",
            session.id, session.order_id, razorpay_order_id, razorpay_payment_id, amount_paise,
        )

        if session.order_id:
            try:
                order = await self._load_order(session.order_id, lock=True)
                await self._confirm_order_paid(
                    order,
                    now=now,
                    note=f"webhook:payment.captured / razorpay_payment_id={razorpay_payment_id}",
                )
            except NotFoundException:
                pass  # Order removed — continue

    async def _on_payment_failed(self, payload: dict) -> None:
        """Handle payment.failed webhook event."""
        entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
        razorpay_order_id: str = entity.get("order_id", "")
        error_description: str = entity.get("error_description", "Payment failed at Razorpay.")
        error_code: str = entity.get("error_code", "PAYMENT_FAILED")

        if not razorpay_order_id:
            return

        try:
            session = await self._load_session_by_razorpay_order(razorpay_order_id, lock=True)
        except NotFoundException:
            return

        # Idempotent: already at a terminal state
        if session.status in ("PAID", "FAILED", "CANCELLED"):
            logger.info(
                "Duplicate webhook payment.failed ignored session_id=%s internal_order_id=%s razorpay_order_id=%s payment_status=%s event_type=duplicate_webhook",
                session.id, session.order_id, razorpay_order_id, session.status,
            )
            return

        validate_payment_session_transition(session.status, "FAILED")
        session.status = "FAILED"
        session.failure_reason = error_description
        session.failure_code = error_code
        session.last_webhook_event = "payment.failed"
        await self.db.flush()

        logger.warning(
            "Webhook payment.failed processed session_id=%s internal_order_id=%s razorpay_order_id=%s payment_status=FAILED failure_reason=%s event_type=webhook_payment_failed",
            session.id, session.order_id, razorpay_order_id, error_description,
        )

        if session.order_id:
            try:
                order = await self._load_order(session.order_id, lock=True)
                if order.payment_status not in ("PAID",):
                    validate_order_payment_transition(order.payment_status, "PAYMENT_FAILED")
                    order.payment_status = "PAYMENT_FAILED"
                    timeline = list(order.timeline or [])
                    timeline.append({
                        "event": "PAYMENT_FAILED",
                        "at": _now_utc().isoformat(),
                        "note": f"webhook:payment.failed / {error_description}",
                    })
                    order.timeline = timeline
                    await self.db.flush()
            except NotFoundException:
                pass

    async def _on_order_paid(self, payload: dict) -> None:
        """
        Handle order.paid event — a higher-level event from Razorpay indicating
        all payments for the Razorpay order have been captured.

        This is idempotent with payment.captured; we just ensure our records
        are consistent.
        """
        entity = payload.get("payload", {}).get("order", {}).get("entity", {})
        razorpay_order_id: str = entity.get("id", "")

        if not razorpay_order_id:
            return

        try:
            session = await self._load_session_by_razorpay_order(razorpay_order_id, lock=True)
        except NotFoundException:
            return

        # Nothing to do if already PAID
        if session.status == "PAID":
            return

        # Update to PAID if in a pre-terminal state
        if session.status in ("CREATED", "PENDING"):
            validate_payment_session_transition(session.status, "PAID", has_verified_evidence=True)
            session.status = "PAID"
            session.paid_at = _now_utc()
            session.last_webhook_event = "order.paid"
            await self.db.flush()

            if session.order_id:
                try:
                    order = await self._load_order(session.order_id, lock=True)
                    await self._confirm_order_paid(order, note="webhook:order.paid")
                except NotFoundException:
                    pass

    async def _on_refund_processed(self, payload: dict) -> None:
        """
        Handle refund.processed event from Razorpay.

        Update session and order payment status to REFUNDED / PARTIALLY_REFUNDED
        idempotently.
        """
        entity = payload.get("payload", {}).get("refund", {}).get("entity", {})
        payment_id: str = entity.get("payment_id", "")
        refund_id: str = entity.get("id", "")
        refund_amount_paise: int = int(entity.get("amount", 0))

        if not payment_id:
            return

        stmt = select(PaymentSessionModel).where(
            PaymentSessionModel.razorpay_payment_id == payment_id
        ).with_for_update()
        result = await self.db.execute(stmt)
        session = result.scalars().first()

        if not session:
            return

        current_refunded = session.refunded_amount_paise or 0
        new_cumulative = max(current_refunded, refund_amount_paise)
        target_status = "REFUNDED" if new_cumulative >= session.amount_paise else "PARTIALLY_REFUNDED"

        # Idempotent: skip if already at or beyond target status
        if session.status in (target_status, "REFUNDED"):
            return

        if session.status in ("PAID", "PARTIALLY_REFUNDED", "REFUND_PENDING"):
            validate_payment_session_transition(session.status, target_status)
            session.status = target_status
            session.refunded_amount_paise = new_cumulative
            session.last_webhook_event = "refund.processed"
            await self.db.flush()

        if session.order_id:
            try:
                order = await self._load_order(session.order_id, lock=True)
                order_target_status = "REFUNDED" if target_status == "REFUNDED" else "PARTIALLY_REFUNDED"
                if order.payment_status not in (order_target_status, "REFUNDED"):
                    validate_order_payment_transition(order.payment_status, order_target_status)
                    order.payment_status = order_target_status
                    timeline = list(order.timeline or [])
                    timeline.append({
                        "event": f"REFUND_{order_target_status}",
                        "at": _now_utc().isoformat(),
                        "note": f"webhook:refund.processed / refund_id={refund_id}",
                    })
                    order.timeline = timeline
                    await self.db.flush()
            except NotFoundException:
                pass

    async def refund_payment(
        self,
        session_id: str,
        amount_paise: Optional[int] = None,
        reason: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        is_admin: bool = False,
    ) -> dict:
        """
        POST /payments/session/{session_id}/refund — process Razorpay refund.

        Rules & Safeguards (Phase 10):
          1. Admin authorization check (raises ForbiddenException if not admin).
          2. Lock PaymentSessionModel row (with_for_update).
          3. Validates session status is PAID or PARTIALLY_REFUNDED.
             (Raises BusinessLogicException if ALREADY fully REFUNDED or non-refundable).
          4. Idempotency guard via idempotency_key.
          5. Amount validation:
             - amount_paise must be > 0 (if specified).
             - total cumulative refunded (existing + requested) MUST NOT exceed amount_paise (total captured).
          6. Non-blocking call to Razorpay Refund API (`client.payment.refund`).
             - Handles timeouts and API errors cleanly.
          7. Updates session status (`PARTIALLY_REFUNDED` or `REFUNDED`),
             `refunded_amount_paise`, and order payment_status atomically.
        """
        if not is_admin:
            logger.warning(
                "Unauthorized refund attempt session_id=%s failure_reason=%s event_type=unauthorized_refund",
                session_id, "Non-admin user requested refund",
            )
            raise ForbiddenException("Only administrators can initiate refunds.")

        session = await self._load_session(session_id, lock=True)

        # Idempotency check — retried idempotent requests return cached response
        if idempotency_key and session.last_webhook_event == f"refund:{idempotency_key}":
            return {
                "ok": True,
                "message": "Refund request already processed (idempotent replay).",
                "refund_id": f"rfnd_{idempotency_key[:8]}",
                "amount_paise": amount_paise or (session.amount_paise - (session.refunded_amount_paise or 0)),
                "status": session.status,
                "order_payment_status": session.status,
            }

        if session.status == "REFUNDED":
            raise BusinessLogicException("Payment has already been fully refunded.")

        if session.status not in ("PAID", "PARTIALLY_REFUNDED", "REFUND_PENDING"):
            raise BusinessLogicException(
                f"Cannot refund payment session in status '{session.status}'."
            )

        if not session.razorpay_payment_id:
            raise BusinessLogicException(
                "No Razorpay payment ID associated with this session."
            )

        total_captured = session.amount_paise
        current_refunded = session.refunded_amount_paise or 0
        remaining_refundable = total_captured - current_refunded

        req_amount = remaining_refundable if amount_paise is None else amount_paise

        if req_amount <= 0:
            raise BusinessLogicException("Refund amount must be greater than zero.")

        if req_amount > remaining_refundable:
            raise BusinessLogicException(
                f"Refund amount ({req_amount} paise) exceeds remaining captured payment amount ({remaining_refundable} paise)."
            )

        refund_data = {
            "amount": req_amount,
            "notes": {"reason": reason or "Customer refund request", "session_id": session.id},
        }

        try:
            client = _build_razorpay_client()
            loop = asyncio.get_running_loop()
            rzp_refund = await loop.run_in_executor(
                None, lambda: client.payment.refund(session.razorpay_payment_id, refund_data)
            )
        except (TimeoutError, asyncio.TimeoutError) as exc:
            logger.error(
                "Razorpay refund timed out session_id=%s internal_order_id=%s razorpay_payment_id=%s failure_reason=%s event_type=refund_timeout",
                session_id, session.order_id, session.razorpay_payment_id, exc,
            )
            raise BusinessLogicException(
                "Razorpay payment gateway connection timed out. Please try again."
            ) from exc
        except Exception as exc:
            err_msg = str(exc)
            if "timeout" in err_msg.lower():
                logger.error(
                    "Razorpay refund timed out session_id=%s internal_order_id=%s razorpay_payment_id=%s failure_reason=%s event_type=refund_timeout",
                    session_id, session.order_id, session.razorpay_payment_id, exc,
                )
                raise BusinessLogicException(
                    "Razorpay payment gateway connection timed out. Please try again."
                ) from exc
            logger.error(
                "Razorpay refund failed session_id=%s internal_order_id=%s razorpay_payment_id=%s failure_reason=%s event_type=refund_failed",
                session_id, session.order_id, session.razorpay_payment_id, exc, exc_info=True,
            )
            raise BusinessLogicException(f"Failed to process refund: {exc}") from exc

        refund_id = rzp_refund.get("id", f"rfnd_{_new_uuid()[:8]}")
        new_cumulative = current_refunded + req_amount
        target_status = "REFUNDED" if new_cumulative >= total_captured else "PARTIALLY_REFUNDED"

        validate_payment_session_transition(session.status, target_status)
        session.status = target_status
        session.refunded_amount_paise = new_cumulative
        if idempotency_key:
            session.last_webhook_event = f"refund:{idempotency_key}"
        await self.db.flush()

        logger.info(
            "Refund processed successfully session_id=%s internal_order_id=%s razorpay_payment_id=%s refund_id=%s amount_paise=%s payment_status=%s event_type=refund_success",
            session.id, session.order_id, session.razorpay_payment_id, refund_id, req_amount, target_status,
        )

        order_payment_status = target_status
        if session.order_id:
            try:
                order = await self._load_order(session.order_id, lock=True)
                if order.payment_status not in (target_status, "REFUNDED"):
                    validate_order_payment_transition(order.payment_status, target_status)
                    order.payment_status = target_status
                    timeline = list(order.timeline or [])
                    timeline.append({
                        "event": f"REFUND_{target_status}",
                        "at": _now_utc().isoformat(),
                        "note": f"refund:{refund_id} / amount_paise={req_amount}",
                    })
                    order.timeline = timeline
                    await self.db.flush()
            except NotFoundException:
                pass

        return {
            "ok": True,
            "message": f"Refund of ₹{req_amount / 100:.2f} processed successfully.",
            "refund_id": refund_id,
            "amount_paise": req_amount,
            "status": target_status,
            "order_payment_status": order_payment_status,
        }

    async def audit_and_reconcile_session(
        self,
        session_id: str,
        is_admin: bool = False,
    ) -> dict:
        """
        Phase 13 Comprehensive Payment Reconciliation Engine.

        Audits and reconciles a single session against Razorpay:
          1. Detects mismatches between Razorpay and PostgreSQL:
             - Razorpay = CAPTURED & DB = PENDING/CREATED → Safe Auto-Recovery → DB=PAID
             - Razorpay = REFUNDED & DB = PAID → Safe Auto-Recovery → DB=REFUNDED
             - DB = PAID & Razorpay = FAILED/NONE → Unsafe Case → Flags requires_admin_review=True (no silent overwrite)
             - Amount Mismatch → Unsafe Case → Flags requires_admin_review=True
          2. Idempotent execution — running multiple times produces identical outcome.
        """
        session = await self._load_session(session_id, lock=True)
        order = await self._load_order(session.order_id, lock=True)

        if not session.razorpay_order_id:
            return {
                "session_id": session.id,
                "order_id": session.order_id,
                "db_status": session.status,
                "razorpay_status": "NONE",
                "reconciled": False,
                "requires_admin_review": False,
                "message": "No Razorpay order associated with this session.",
            }

        payments = await self._fetch_razorpay_order_payments(session.razorpay_order_id)
        captured = next((p for p in payments if p.get("status") == "captured"), None)
        refunded = next((p for p in payments if p.get("refund_status") == "full" or p.get("amount_refunded", 0) >= session.amount_paise), None)
        failed = next((p for p in payments if p.get("status") == "failed"), None)

        razorpay_status = "NONE"
        if captured:
            razorpay_status = "CAPTURED"
        elif refunded:
            razorpay_status = "REFUNDED"
        elif failed:
            razorpay_status = "FAILED"

        # ── Safe Auto-Recovery Scenario A: Razorpay CAPTURED, DB PENDING ─────────────
        if captured and session.status in ("CREATED", "PENDING"):
            amount_paise = int(captured.get("amount", 0))
            if amount_paise != session.amount_paise:
                logger.warning(
                    "Reconciliation flagged amount mismatch session_id=%s db_amount=%s rzp_amount=%s",
                    session.id, session.amount_paise, amount_paise,
                )
                return {
                    "session_id": session.id,
                    "order_id": session.order_id,
                    "db_status": session.status,
                    "razorpay_status": "CAPTURED",
                    "reconciled": False,
                    "requires_admin_review": True,
                    "message": f"Amount mismatch: DB expected {session.amount_paise} paise, Razorpay has {amount_paise} paise.",
                }

            now = _now_utc()
            validate_payment_session_transition(session.status, "PAID", has_verified_evidence=True)
            session.status = "PAID"
            session.razorpay_payment_id = captured.get("id")
            session.paid_at = now
            session.last_webhook_event = "audit:captured"
            await self.db.flush()

            await self._confirm_order_paid(order, now=now, note="audit:reconciled_captured")

            return {
                "session_id": session.id,
                "order_id": session.order_id,
                "db_status": "PAID",
                "razorpay_status": "CAPTURED",
                "reconciled": True,
                "requires_admin_review": False,
                "message": "Safely recovered session to PAID.",
            }

        # ── Safe Auto-Recovery Scenario B: Razorpay REFUNDED, DB PAID ────────────────
        if refunded and session.status in ("PAID", "PARTIALLY_REFUNDED"):
            validate_payment_session_transition(session.status, "REFUNDED")
            session.status = "REFUNDED"
            session.refunded_amount_paise = session.amount_paise
            session.last_webhook_event = "audit:refunded"
            await self.db.flush()

            order_target = "REFUNDED"
            if order.payment_status not in ("REFUNDED",):
                validate_order_payment_transition(order.payment_status, order_target)
                order.payment_status = order_target
                await self.db.flush()

            return {
                "session_id": session.id,
                "order_id": session.order_id,
                "db_status": "REFUNDED",
                "razorpay_status": "REFUNDED",
                "reconciled": True,
                "requires_admin_review": False,
                "message": "Safely recovered session to REFUNDED.",
            }

        # ── Unsafe Mismatch Scenario C: DB PAID, Razorpay FAILED or NONE ─────────────
        if session.status == "PAID" and razorpay_status in ("FAILED", "NONE"):
            logger.warning(
                "UNSAFE MISMATCH FLAGGED FOR ADMIN REVIEW session_id=%s db_status=%s razorpay_status=%s",
                session.id, session.status, razorpay_status,
            )
            return {
                "session_id": session.id,
                "order_id": session.order_id,
                "db_status": session.status,
                "razorpay_status": razorpay_status,
                "reconciled": False,
                "requires_admin_review": True,
                "message": f"DB indicates PAID but Razorpay status is {razorpay_status}. Preserving DB record for admin audit.",
            }

        # ── Clean / Matching Status ───────────────────────────────────────────────
        return {
            "session_id": session.id,
            "order_id": session.order_id,
            "db_status": session.status,
            "razorpay_status": razorpay_status,
            "reconciled": False,
            "requires_admin_review": False,
            "message": f"Session status '{session.status}' matches Razorpay state.",
        }

    async def reconcile_batch(
        self,
        limit: int = 50,
        session_ids: Optional[list[str]] = None,
        is_admin: bool = False,
    ) -> dict:
        """
        Batch payment reconciliation for background job execution or admin desk.
        Scans non-terminal payment sessions, audits against Razorpay, auto-recovers
        safe cases and flags unsafe cases for admin review.
        """
        if not is_admin:
            raise ForbiddenException("Only administrators can execute batch reconciliation.")

        if session_ids:
            stmt = select(PaymentSessionModel.id).where(PaymentSessionModel.id.in_(session_ids)).limit(limit)
        else:
            stmt = (
                select(PaymentSessionModel.id)
                .where(PaymentSessionModel.status.in_(["CREATED", "PENDING"]))
                .limit(limit)
            )

        result = await self.db.execute(stmt)
        ids = result.scalars().all()

        scanned = len(ids)
        reconciled_count = 0
        flagged_count = 0
        details = []

        for sid in ids:
            try:
                res = await self.audit_and_reconcile_session(sid, is_admin=is_admin)
                details.append(res)
                if res.get("reconciled"):
                    reconciled_count += 1
                if res.get("requires_admin_review"):
                    flagged_count += 1
            except Exception as exc:
                logger.error("Error during batch reconciliation session_id=%s error=%s", sid, exc)
                details.append({
                    "session_id": sid,
                    "reconciled": False,
                    "requires_admin_review": True,
                    "message": f"Reconciliation exception: {exc}",
                })
                flagged_count += 1

        return {
            "ok": True,
            "scanned": scanned,
            "reconciled": reconciled_count,
            "flagged_for_admin": flagged_count,
            "details": details,
        }



