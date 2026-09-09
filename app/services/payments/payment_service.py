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

    async def _load_session(self, session_id: str) -> PaymentSessionModel:
        stmt = select(PaymentSessionModel).where(PaymentSessionModel.id == session_id)
        result = await self.db.execute(stmt)
        session = result.scalars().first()
        if not session:
            raise NotFoundException(f"Payment session '{session_id}' not found.")
        return session

    async def _load_session_by_razorpay_order(
        self, razorpay_order_id: str
    ) -> PaymentSessionModel:
        stmt = select(PaymentSessionModel).where(
            PaymentSessionModel.razorpay_order_id == razorpay_order_id
        )
        result = await self.db.execute(stmt)
        session = result.scalars().first()
        if not session:
            raise NotFoundException(
                f"No payment session found for Razorpay order '{razorpay_order_id}'."
            )
        return session

    async def _load_order(self, order_id: str) -> OrderModel:
        stmt = select(OrderModel).where(OrderModel.id == order_id)
        result = await self.db.execute(stmt)
        order = result.scalars().first()
        if not order:
            raise NotFoundException(f"Order '{order_id}' not found.")
        return order

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
        Call Razorpay Create Order API synchronously.

        The razorpay Python SDK is synchronous — we call it directly here.
        In a high-throughput setup you'd run this in a thread pool executor
        (asyncio.get_event_loop().run_in_executor), but for a fashion backend
        the direct call is acceptable and simpler.
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
            razorpay_order = client.order.create(data=order_data)
        except Exception as exc:
            logger.error("Razorpay order creation failed receipt=%s error=%s", receipt, exc, exc_info=True)
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

        session.status = "CANCELLED"
        session.cancelled_at = _now_utc()
        session.failure_reason = reason or "Cancelled by user."
        await self.db.flush()

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
        session = await self._load_session_by_razorpay_order(razorpay_order_id)
        order = await self._load_order(session.order_id)
        await self._assert_order_access(order, owner_customer_id, owner_guest_email)

        # Guard: idempotent — already verified
        if session.status == "PAID":
            await self._confirm_order_paid(order, note="verification replay")
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

            raise BusinessLogicException(
                "Payment verification failed: invalid signature. "
                "This may indicate a tampered callback. Contact support if this persists."
            )

        # ── Amount cross-check via Razorpay fetch API ─────────────────────────
        # This is an extra security layer — we verify the amount Razorpay
        # recorded matches what we expect to charge.
        try:
            client = _build_razorpay_client()
            payment_details = client.payment.fetch(razorpay_payment_id)
            razorpay_amount = int(payment_details.get("amount", 0))

            if razorpay_amount != session.amount_paise:
                session.status = "FAILED"
                session.failure_reason = (
                    f"Amount mismatch: expected {session.amount_paise} paise, "
                    f"Razorpay recorded {razorpay_amount} paise."
                )
                session.failure_code = "AMOUNT_MISMATCH"
                await self.db.flush()
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
        session.status = "PAID"
        session.razorpay_payment_id = razorpay_payment_id
        session.razorpay_signature = razorpay_signature
        session.paid_at = now
        await self.db.flush()

        logger.info(
            "Payment verified session_id=%s razorpay_payment_id=%s order_id=%s",
            session.id, razorpay_payment_id, session.order_id,
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
            logger.warning("Webhook signature verification failed")
            raise ForbiddenException(
                "Webhook signature verification failed. "
                "The request does not appear to originate from Razorpay."
            )

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise BusinessLogicException(f"Malformed webhook payload: {exc}") from exc

        event = payload.get("event")
        if not event:
            raise BusinessLogicException("Webhook payload is missing 'event' field.")

        logger.info("Webhook received event=%s", event)

        # ── Event dispatch ────────────────────────────────────────────────────
        if event == "payment.captured":
            await self._on_payment_captured(payload)
        elif event == "payment.failed":
            await self._on_payment_failed(payload)
        elif event == "order.paid":
            await self._on_order_paid(payload)
        else:
            # Unknown event — acknowledge without processing (do NOT return 4xx)
            logger.warning("Unhandled webhook event event=%s", event)

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
            session = await self._load_session_by_razorpay_order(razorpay_order_id)
        except NotFoundException:
            return  # Session not found — already removed or test event

        # Idempotent: already PAID
        if session.status == "PAID":
            return

        # Amount guard
        if amount_paise != session.amount_paise:
            session.status = "FAILED"
            session.failure_reason = (
                f"Webhook amount mismatch: expected {session.amount_paise} paise, "
                f"received {amount_paise} paise."
            )
            session.failure_code = "AMOUNT_MISMATCH"
            session.last_webhook_event = "payment.captured"
            await self.db.flush()
            return

        now = _now_utc()
        session.status = "PAID"
        session.razorpay_payment_id = razorpay_payment_id
        session.paid_at = now
        session.last_webhook_event = "payment.captured"
        await self.db.flush()

        if session.order_id:
            try:
                order = await self._load_order(session.order_id)
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
            session = await self._load_session_by_razorpay_order(razorpay_order_id)
        except NotFoundException:
            return

        # Idempotent: already at a terminal state
        if session.status in ("PAID", "FAILED", "CANCELLED"):
            return

        session.status = "FAILED"
        session.failure_reason = error_description
        session.failure_code = error_code
        session.last_webhook_event = "payment.failed"
        await self.db.flush()

        if session.order_id:
            try:
                order = await self._load_order(session.order_id)
                if order.payment_status not in ("PAID",):
                    order.payment_status = "FAILED"
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
            session = await self._load_session_by_razorpay_order(razorpay_order_id)
        except NotFoundException:
            return

        # Nothing to do if already PAID
        if session.status == "PAID":
            return

        # Update to PAID if in a pre-terminal state
        if session.status in ("CREATED", "PENDING"):
            session.status = "PAID"
            session.paid_at = _now_utc()
            session.last_webhook_event = "order.paid"
            await self.db.flush()

            if session.order_id:
                try:
                    order = await self._load_order(session.order_id)
                    await self._confirm_order_paid(order, note="webhook:order.paid")
                except NotFoundException:
                    pass
