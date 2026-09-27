"""
Domain Enums and State Transition Rules for Payments and Orders.

PaymentSessionStatus:
  CREATED            → Initial session state
  PENDING            → Client payment modal opened
  AUTHORIZED         → Payment authorized by bank (pre-capture)
  CAPTURED           → Payment captured successfully (equivalent to PAID)
  PAID               → Legacy/canonical paid status
  FAILED             → Payment failed or signature mismatch
  CANCELLED          → Explicitly cancelled before payment
  EXPIRED            → TTL expired
  REFUND_PENDING     → Refund requested/initiated
  PARTIALLY_REFUNDED → Partial refund processed
  REFUNDED           → Full refund processed

OrderPaymentStatus:
  PENDING_PAYMENT    → Order placed, payment pending
  PAYMENT_PROCESSING → Payment in progress
  PAID               → Payment confirmed
  PAYMENT_FAILED     → Payment attempt failed
  PAYMENT_CANCELLED  → Payment cancelled
  PAYMENT_EXPIRED    → Payment session expired
  REFUND_PENDING     → Refund in progress
  PARTIALLY_REFUNDED → Partial refund completed
  REFUNDED           → Full refund completed
"""

from enum import Enum
from typing import Set

from app.core.exceptions import BusinessLogicException


class PaymentSessionStatus(str, Enum):
    CREATED = "CREATED"
    PENDING = "PENDING"
    AUTHORIZED = "AUTHORIZED"
    CAPTURED = "CAPTURED"
    PAID = "PAID"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    REFUND_PENDING = "REFUND_PENDING"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"
    REFUNDED = "REFUNDED"


class OrderPaymentStatus(str, Enum):
    PENDING_PAYMENT = "PENDING_PAYMENT"
    PAYMENT_PROCESSING = "PAYMENT_PROCESSING"
    PAID = "PAID"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    PAYMENT_CANCELLED = "PAYMENT_CANCELLED"
    PAYMENT_EXPIRED = "PAYMENT_EXPIRED"
    REFUND_PENDING = "REFUND_PENDING"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"
    REFUNDED = "REFUNDED"


# ---------------------------------------------------------------------------
# Allowed State Transitions
# ---------------------------------------------------------------------------

ALLOWED_PAYMENT_SESSION_TRANSITIONS: dict[str, Set[str]] = {
    PaymentSessionStatus.CREATED: {
        PaymentSessionStatus.PENDING,
        PaymentSessionStatus.AUTHORIZED,
        PaymentSessionStatus.CAPTURED,
        PaymentSessionStatus.PAID,
        PaymentSessionStatus.FAILED,
        PaymentSessionStatus.CANCELLED,
        PaymentSessionStatus.EXPIRED,
    },
    PaymentSessionStatus.PENDING: {
        PaymentSessionStatus.AUTHORIZED,
        PaymentSessionStatus.CAPTURED,
        PaymentSessionStatus.PAID,
        PaymentSessionStatus.FAILED,
        PaymentSessionStatus.CANCELLED,
        PaymentSessionStatus.EXPIRED,
    },
    PaymentSessionStatus.AUTHORIZED: {
        PaymentSessionStatus.CAPTURED,
        PaymentSessionStatus.PAID,
        PaymentSessionStatus.FAILED,
        PaymentSessionStatus.CANCELLED,
        PaymentSessionStatus.EXPIRED,
    },
    PaymentSessionStatus.CAPTURED: {
        PaymentSessionStatus.REFUND_PENDING,
        PaymentSessionStatus.PARTIALLY_REFUNDED,
        PaymentSessionStatus.REFUNDED,
    },
    PaymentSessionStatus.PAID: {
        PaymentSessionStatus.REFUND_PENDING,
        PaymentSessionStatus.PARTIALLY_REFUNDED,
        PaymentSessionStatus.REFUNDED,
    },
    PaymentSessionStatus.REFUND_PENDING: {
        PaymentSessionStatus.PARTIALLY_REFUNDED,
        PaymentSessionStatus.REFUNDED,
        PaymentSessionStatus.FAILED,
    },
    PaymentSessionStatus.PARTIALLY_REFUNDED: {
        PaymentSessionStatus.REFUNDED,
    },
    # Terminal states
    PaymentSessionStatus.FAILED: set(),
    PaymentSessionStatus.CANCELLED: set(),
    PaymentSessionStatus.EXPIRED: set(),
    PaymentSessionStatus.REFUNDED: set(),
}

ALLOWED_ORDER_PAYMENT_TRANSITIONS: dict[str, Set[str]] = {
    OrderPaymentStatus.PENDING_PAYMENT: {
        OrderPaymentStatus.PAYMENT_PROCESSING,
        OrderPaymentStatus.PAID,
        OrderPaymentStatus.PAYMENT_FAILED,
        OrderPaymentStatus.PAYMENT_CANCELLED,
        OrderPaymentStatus.PAYMENT_EXPIRED,
    },
    "PENDING": {
        OrderPaymentStatus.PAYMENT_PROCESSING,
        OrderPaymentStatus.PAID,
        OrderPaymentStatus.PAYMENT_FAILED,
        OrderPaymentStatus.PAYMENT_CANCELLED,
        OrderPaymentStatus.PAYMENT_EXPIRED,
    },
    OrderPaymentStatus.PAYMENT_PROCESSING: {
        OrderPaymentStatus.PAID,
        OrderPaymentStatus.PAYMENT_FAILED,
        OrderPaymentStatus.PAYMENT_CANCELLED,
        OrderPaymentStatus.PAYMENT_EXPIRED,
    },
    OrderPaymentStatus.PAID: {
        OrderPaymentStatus.REFUND_PENDING,
        OrderPaymentStatus.PARTIALLY_REFUNDED,
        OrderPaymentStatus.REFUNDED,
    },
    OrderPaymentStatus.REFUND_PENDING: {
        OrderPaymentStatus.PARTIALLY_REFUNDED,
        OrderPaymentStatus.REFUNDED,
        OrderPaymentStatus.PAYMENT_FAILED,
    },
    OrderPaymentStatus.PARTIALLY_REFUNDED: {
        OrderPaymentStatus.REFUNDED,
    },
    # Terminal states
    OrderPaymentStatus.PAYMENT_FAILED: set(),
    OrderPaymentStatus.PAYMENT_CANCELLED: set(),
    OrderPaymentStatus.PAYMENT_EXPIRED: set(),
    OrderPaymentStatus.REFUNDED: set(),
}


def validate_payment_session_transition(
    current_status: str,
    target_status: str,
    has_verified_evidence: bool = False,
) -> bool:
    """
    Validate payment session status transition.

    Rules:
      1. Replay of same status is allowed (idempotent).
      2. If target is in ALLOWED_PAYMENT_SESSION_TRANSITIONS[current], transition is valid.
      3. Transitioning from FAILED to PAID/CAPTURED is ONLY allowed if
         has_verified_evidence is True (e.g. valid HMAC signature + Razorpay fetch).
    """
    if current_status == target_status:
        return True

    # Check terminal FAILED exception for verified evidence
    if (
        current_status == PaymentSessionStatus.FAILED
        and target_status in (PaymentSessionStatus.PAID, PaymentSessionStatus.CAPTURED)
    ):
        if not has_verified_evidence:
            raise BusinessLogicException(
                f"Cannot transition payment session from '{current_status}' to '{target_status}' "
                "without verified payment signature evidence."
            )
        return True

    allowed = ALLOWED_PAYMENT_SESSION_TRANSITIONS.get(current_status, set())
    if target_status not in allowed:
        raise BusinessLogicException(
            f"Invalid payment session status transition: '{current_status}' → '{target_status}'."
        )
    return True


def validate_order_payment_transition(
    current_status: str,
    target_status: str,
    has_verified_evidence: bool = False,
) -> bool:
    """
    Validate order payment status transition.

    Rules:
      1. Replay of same status is allowed (idempotent).
      2. If target is in ALLOWED_ORDER_PAYMENT_TRANSITIONS[current], transition is valid.
      3. Transitioning from PAYMENT_FAILED to PAID is ONLY allowed if
         has_verified_evidence is True.
    """
    if current_status == target_status:
        return True

    if (
        current_status == OrderPaymentStatus.PAYMENT_FAILED
        and target_status == OrderPaymentStatus.PAID
    ):
        if not has_verified_evidence:
            raise BusinessLogicException(
                f"Cannot transition order payment status from '{current_status}' to '{target_status}' "
                "without verified payment evidence."
            )
        return True

    allowed = ALLOWED_ORDER_PAYMENT_TRANSITIONS.get(current_status, set())
    if target_status not in allowed:
        raise BusinessLogicException(
            f"Invalid order payment status transition: '{current_status}' → '{target_status}'."
        )
    return True
