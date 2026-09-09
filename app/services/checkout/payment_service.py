"""
PaymentService — Razorpay gateway integration and payment verification.

Cache integration
─────────────────────────────────────────────────────────────
  idempotency:{key}   Re-used from CheckoutService — same 24-hour cache.
                      Any payment verification or webhook event that carries
                      an Idempotency-Key header is served from cache on retry.

Security requirements (Requirement 9.5–9.8)
─────────────────────────────────────────────────────────────
  - Verify HMAC-SHA256 signature using RAZORPAY_KEY_SECRET before marking
    a payment as successful.
  - Verify webhook signature using RAZORPAY_WEBHOOK_SECRET.
  - Process each payment event idempotently — duplicate events must NOT
    create duplicate orders or payment records.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.cache import TTL_IDEMPOTENCY, cache

logger = logging.getLogger(__name__)


class PaymentService:
    """Business logic for Razorpay gateway operations."""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    # ── Idempotency helpers ───────────────────────────────────────────────────

    @staticmethod
    def _idempotency_key(key: str) -> str:
        return f"idempotency:{key}"

    async def get_idempotent_response(self, idempotency_key: Optional[str]) -> Optional[Dict[str, Any]]:
        """Return the cached response for *idempotency_key*, or None on miss."""
        if not idempotency_key:
            return None
        return await cache.get_json(self._idempotency_key(idempotency_key))

    async def save_idempotent_response(
        self, idempotency_key: Optional[str], response: Dict[str, Any]
    ) -> None:
        """Store *response* under *idempotency_key* with TTL_IDEMPOTENCY seconds TTL."""
        if not idempotency_key:
            return
        await cache.set_json(
            self._idempotency_key(idempotency_key),
            response,
            TTL_IDEMPOTENCY,
        )

    # ── Signature verification ────────────────────────────────────────────────

    def verify_payment_signature(
        self,
        razorpay_order_id: str,
        razorpay_payment_id: str,
        razorpay_signature: str,
    ) -> bool:
        """
        Verify the Razorpay payment signature (Requirement 9.5).

        Expected signature = HMAC-SHA256(
            key=RAZORPAY_KEY_SECRET,
            msg="{razorpay_order_id}|{razorpay_payment_id}"
        )
        """
        key_secret = settings.RAZORPAY_KEY_SECRET or ""
        message = f"{razorpay_order_id}|{razorpay_payment_id}"
        expected = hmac.new(
            key_secret.encode(),
            message.encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, razorpay_signature)

    def verify_webhook_signature(self, payload_body: bytes, razorpay_signature: str) -> bool:
        """
        Verify a Razorpay webhook signature (Requirement 9.7).

        Expected signature = HMAC-SHA256(
            key=RAZORPAY_WEBHOOK_SECRET,
            msg=raw_request_body_bytes
        )
        """
        webhook_secret = settings.RAZORPAY_WEBHOOK_SECRET or ""
        expected = hmac.new(
            webhook_secret.encode(),
            payload_body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, razorpay_signature)

    # ── Payment verification ──────────────────────────────────────────────────

    async def verify_payment(
        self,
        razorpay_order_id: str,
        razorpay_payment_id: str,
        razorpay_signature: str,
        customer_id: str,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        POST /checkout/verify-payment

        1. Check idempotency cache — replay if duplicate.
        2. Verify HMAC-SHA256 signature.
        3. Mark payment as CAPTURED.
        4. Trigger atomic order creation.
        5. Cache and return result.
        """
        # Step 1 — idempotency
        cached = await self.get_idempotent_response(idempotency_key)
        if cached:
            logger.info("Replaying idempotent verify_payment key=%s", idempotency_key)
            return cached

        # Step 2 — signature verification
        if not self.verify_payment_signature(
            razorpay_order_id, razorpay_payment_id, razorpay_signature
        ):
            # Do NOT cache failed signature responses
            return {
                "ok": False,
                "error": "Payment signature verification failed.",
            }

        # TODO: Steps 3–4 — mark PaymentModel as CAPTURED, create Order
        result: Dict[str, Any] = {
            "ok": True,
            "razorpay_order_id": razorpay_order_id,
            "razorpay_payment_id": razorpay_payment_id,
            "status": "CAPTURED",
        }

        # Step 5 — cache on success only
        await self.save_idempotent_response(idempotency_key, result)
        return result

    async def handle_webhook(
        self,
        payload_body: bytes,
        razorpay_signature: str,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        POST /webhooks/razorpay

        Idempotent webhook handler — processes each event exactly once.
        Uses ``idempotency:{razorpay_event_id}`` to prevent duplicate order creation.
        """
        # Signature check
        if not self.verify_webhook_signature(payload_body, razorpay_signature):
            return {"ok": False, "error": "Webhook signature verification failed."}

        import json as _json
        try:
            payload = _json.loads(payload_body)
        except Exception as exc:
            logger.warning("Webhook payload JSON parse failed: %s", exc)
            return {"ok": False, "error": "Invalid webhook payload."}

        event_id = payload.get("id") or idempotency_key
        cached = await self.get_idempotent_response(event_id)
        if cached:
            logger.info("Replaying idempotent webhook event_id=%s", event_id)
            return cached
        event_type = payload.get("event", "")
        result: Dict[str, Any] = {"ok": True, "event": event_type}

        if event_type == "payment.captured":
            # TODO: extract payment_id, order_id; create order if not exists
            logger.info("Received payment.captured webhook event_id=%s", event_id)
            result["status"] = "processed"

        await self.save_idempotent_response(event_id, result)
        return result
