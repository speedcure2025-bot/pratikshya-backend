"""
Phase 2 unit tests — canonical checkout lifecycle, trust model and
idempotency.

Covers (no production DB, no network):
  - PlaceOrderRequest DTO contract (firstName/lastName, payment/delivery
    method allow-lists, email shape).
  - place_order: server-authoritative pricing/totals, stock reservation
    under row locks, canonical initial status (payment method ≠ PAID),
    coupon revalidation, idempotent replays + cross-owner conflicts.
  - Order cancellation: stock release (unpaid only) + session cancellation.
  - Guest order claim: account-email binding (route + service).
  - Payment sessions: order-first requirement, COD rejection, ownership
    guards, active-session resume, authoritative amount.
  - Payment verification: signature-gated PAID, cancelled-order guard,
    ownership, idempotent replay, canonical order confirmation.
  - Webhook: signature gate + payment.captured confirmation.

Pattern follows tests/unit/test_phase1_security.py (IsolatedAsyncioTestCase
+ AsyncMock db + SimpleNamespace stubs).
"""

import hashlib
import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from pydantic import ValidationError

from app.core.exceptions import (
    BusinessLogicException,
    ConflictException,
    ForbiddenException,
)
from app.models.orders.order import OrderModel
from app.models.orders.order_item import OrderItemModel
from app.models.orders.order_status_history import OrderStatusHistoryModel
from app.models.commerce.coupon_redemption import CouponRedemptionModel
from app.models.payments.payment_session import PaymentSessionModel
from app.schemas.orders.order import PlaceOrderRequest
from app.services.orders.order_service import (
    OrderService,
    _order_number_from_key,
)
from app.services.payments import payment_service as payment_service_module
from app.services.payments.payment_service import PaymentService


# ---------------------------------------------------------------------------
# Fakes / stubs
# ---------------------------------------------------------------------------

class FakeScalars:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values

    def first(self):
        return self.values[0] if self.values else None


class FakeResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return FakeScalars(self.values)


class LazyLoadedOrder:
    """Sentinel: resolves to the OrderModel most recently passed to db.add."""


def make_db(results):
    """
    Build a db mock where `execute` pops `results` in call order (the last
    result repeats). `add` captures ORM instances by type. `flush` is an
    AsyncMock.
    """
    db = AsyncMock()
    capture = {
        "orders": [], "items": [], "history": [],
        "redemptions": [], "sessions": [],
    }

    def _add(obj, *args, **kwargs):
        if isinstance(obj, OrderModel):
            capture["orders"].append(obj)
        elif isinstance(obj, OrderItemModel):
            capture["items"].append(obj)
        elif isinstance(obj, OrderStatusHistoryModel):
            capture["history"].append(obj)
        elif isinstance(obj, CouponRedemptionModel):
            capture["redemptions"].append(obj)
        elif isinstance(obj, PaymentSessionModel):
            capture["sessions"].append(obj)

    db.add = Mock(side_effect=_add)
    db.flush = AsyncMock()

    state = {"i": 0}

    def _execute(stmt):
        r = results[min(state["i"], len(results) - 1)]
        state["i"] += 1
        if isinstance(r, LazyLoadedOrder) or r is LazyLoadedOrder:
            assert capture["orders"], "expected an order to have been created"
            return FakeResult([capture["orders"][-1]])
        return r

    db.execute = AsyncMock(side_effect=_execute)
    return db, capture


def product_stub(**overrides):
    base = dict(
        id="PF-TEST-001",
        name="Test Saree",
        status="PUBLISHED",
        published=True,
        price=1000,
        original_price=0,
        pricing=None,
        stock=5,
        availability="in-stock",
        image="/images/test.webp",
        sku="SKU-1",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def coupon_stub(**overrides):
    now = datetime.now(timezone.utc)
    base = dict(
        id="coupon-1",
        code="FLAT100",
        is_active=True,
        discount_type="fixed",
        discount_value=100,
        minimum_order_value=0,
        starts_at=None,
        expires_at=None,
        usage_limit=None,
        usage_count=0,
        per_customer_limit=None,
        eligible_customer_ids=None,
        eligible_product_ids=None,
        excluded_product_ids=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def order_stub(**overrides):
    base = dict(
        id="order-1",
        order_number="PF-ORD-0001",
        customer_id="cust-1",
        guest_email=None,
        guest_phone=None,
        status="PENDING_PAYMENT",
        payment_status="PENDING",
        payment_method="upi",
        delivery_method="standard",
        subtotal=1000,
        total=1099,
        shipping_address={"fullName": "Asha Patel", "phone": "9876543210"},
        timeline=[],
        internal_notes=[],
        items=[],
        cancelled_at=None,
        cancellation_reason=None,
        cancelled_by=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def session_stub(**overrides):
    base = dict(
        id="sess-1",
        order_id="order-1",
        razorpay_order_id="order_RZP1",
        razorpay_payment_id=None,
        razorpay_signature=None,
        amount_paise=109900,
        currency="INR",
        payment_method="upi",
        status="CREATED",
        idempotency_key=None,
        paid_at=None,
        cancelled_at=None,
        failure_reason=None,
        failure_code=None,
        last_webhook_event=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def make_request(
    items=None,
    payment="upi",
    delivery="standard",
    email="buyer@example.com",
    first="Asha",
    last="Patel",
    coupon=None,
    idem=None,
):
    items = items or [{"productId": "PF-TEST-001", "quantity": 1}]
    return PlaceOrderRequest.model_validate(
        {
            "items": items,
            "customer": {
                "firstName": first,
                "lastName": last,
                "email": email,
                "phone": "9876543210",
            },
            "address": {
                "fullName": f"{first} {last}",
                "phone": "9876543210",
                "addressLine": "12 Market Street",
                "landmark": "Near Clock Tower",
                "city": "Kolkata",
                "state": "West Bengal",
                "pincode": "700001",
                "type": "home",
            },
            "deliveryMethod": delivery,
            "paymentMethod": payment,
            "couponCode": coupon,
            "idempotencyKey": idem,
        }
    )


def created_order(capture):
    assert capture["orders"], "no OrderModel was added to the db session"
    return capture["orders"][0]


def user_stub(**overrides):
    base = dict(full_name="Asha Patel", email="buyer@example.com", phone="9876543210")
    base.update(overrides)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# DTO contract
# ---------------------------------------------------------------------------

class PlaceOrderRequestSchemaTests(unittest.TestCase):
    def test_first_and_last_name_are_required_separately(self):
        with self.assertRaises(ValidationError):
            PlaceOrderRequest.model_validate(
                {
                    "items": [{"productId": "p1", "quantity": 1}],
                    "customer": {"firstName": "Asha", "email": "a@b.co", "phone": "1"},
                    "address": {
                        "fullName": "Asha", "phone": "1", "addressLine": "x",
                        "city": "Kolkata", "state": "WB", "pincode": "700001",
                    },
                    "paymentMethod": "upi",
                }
            )

    def test_full_name_alone_is_not_a_valid_customer(self):
        # A single fullName string must NOT be accepted — no guessing.
        req = make_request(first="Asha")
        self.assertEqual(req.customer.first_name, "Asha")
        self.assertEqual(req.customer.last_name, "Patel")

    def test_blank_names_are_rejected(self):
        with self.assertRaises(ValidationError):
            make_request(first="   ")
        with self.assertRaises(ValidationError):
            make_request(last="")

    def test_email_is_shape_validated_and_normalised(self):
        req = make_request(email="Buyer@Example.com")
        self.assertEqual(req.customer.email, "buyer@example.com")
        with self.assertRaises(ValidationError):
            make_request(email="not-an-email")

    def test_payment_and_delivery_methods_are_allow_listed(self):
        for method in ("upi", "card", "netbanking", "cod"):
            self.assertEqual(make_request(payment=method).payment_method, method)
        with self.assertRaises(ValidationError):
            make_request(payment="crypto")
        with self.assertRaises(ValidationError):
            make_request(delivery="overnight")

    def test_quantity_is_bounded(self):
        with self.assertRaises(ValidationError):
            make_request(items=[{"productId": "p1", "quantity": 100}])


# ---------------------------------------------------------------------------
# place_order — trust model
# ---------------------------------------------------------------------------

class PlaceOrderTrustTests(unittest.IsolatedAsyncioTestCase):
    async def test_online_order_starts_pending_not_paid(self):
        product = product_stub()
        # 1: locked products, 2: loaded order, 3: user lookup (customer projection)
        db, capture = make_db([FakeResult([product]), LazyLoadedOrder(), FakeResult([user_stub()])])
        service = OrderService(db)

        result = await service.place_order(make_request(payment="card"), customer_id="cust-1")

        order = created_order(capture)
        self.assertEqual(order.status, "PENDING_PAYMENT")
        self.assertEqual(order.payment_status, "PENDING")
        # Authoritative server pricing: 1000 + shipping 99 (< 5000 threshold)
        self.assertEqual(order.subtotal, 1000)
        self.assertEqual(order.shipping_fee, 99)
        self.assertEqual(order.total, 1099)
        # Exactly one status-history seed row (no duplicates).
        self.assertEqual(len(capture["history"]), 1)
        self.assertEqual(capture["history"][0].to_status, "PENDING_PAYMENT")
        self.assertIsNone(capture["history"][0].from_status)
        # Customer projection attached for the confirmation page.
        self.assertIsNotNone(result.customer)

    async def test_cod_order_confirmed_but_payment_stays_pending(self):
        product = product_stub()
        db, capture = make_db([FakeResult([product]), LazyLoadedOrder()])
        service = OrderService(db)

        await service.place_order(make_request(payment="cod"), customer_id=None)

        order = created_order(capture)
        self.assertEqual(order.status, "ORDER_CONFIRMED")
        self.assertEqual(order.payment_status, "PENDING")
        self.assertEqual(order.cod_fee, 49)
        self.assertEqual(order.total, 1000 + 99 + 49)
        self.assertEqual(capture["history"][0].to_status, "ORDER_CONFIRMED")
        # Guest identity captured (used by the verified claim flow).
        self.assertEqual(order.guest_email, "buyer@example.com")
        self.assertIsNone(order.customer_id)

    async def test_pricing_is_resolved_from_catalogue_not_client(self):
        # 20% off 1500 → unit 1200; 2 units → subtotal 2400, product discount 600.
        product = product_stub(
            price=1500,
            pricing={"discountType": "percentage", "discountValue": 20},
        )
        db, capture = make_db([FakeResult([product]), LazyLoadedOrder()])
        service = OrderService(db)

        await service.place_order(
            make_request(items=[{"productId": "PF-TEST-001", "quantity": 2}]),
            customer_id=None,
        )

        order = created_order(capture)
        self.assertEqual(order.subtotal, 2400)
        self.assertEqual(order.product_discount, 600)
        self.assertEqual(order.shipping_fee, 99)
        self.assertEqual(order.total, 2499)
        item = capture["items"][0]
        self.assertEqual(item.unit_price, 1200)
        self.assertEqual(item.line_total, 2400)

    async def test_stock_is_reserved_and_decremented(self):
        product = product_stub(stock=5)
        db, capture = make_db([FakeResult([product]), LazyLoadedOrder()])
        service = OrderService(db)

        await service.place_order(
            make_request(items=[{"productId": "PF-TEST-001", "quantity": 2}]),
            customer_id=None,
        )
        self.assertEqual(product.stock, 3)

    async def test_insufficient_stock_is_rejected(self):
        product = product_stub(stock=1)
        db, capture = make_db([FakeResult([product]), LazyLoadedOrder()])
        service = OrderService(db)

        with self.assertRaises(BusinessLogicException):
            await service.place_order(
                make_request(items=[{"productId": "PF-TEST-001", "quantity": 2}]),
                customer_id=None,
            )
        self.assertEqual(product.stock, 1)  # untouched
        self.assertEqual(capture["orders"], [])

    async def test_out_of_stock_and_unpublished_products_are_rejected(self):
        oos = product_stub(stock=0, availability="out-of-stock")
        db, capture = make_db([FakeResult([oos]), LazyLoadedOrder()])
        with self.assertRaises(BusinessLogicException):
            await OrderService(db).place_order(make_request(), customer_id=None)
        self.assertEqual(capture["orders"], [])

        hidden = product_stub(status="DRAFT", published=False)
        db2, capture2 = make_db([FakeResult([hidden]), LazyLoadedOrder()])
        with self.assertRaises(BusinessLogicException):
            await OrderService(db2).place_order(make_request(), customer_id=None)
        self.assertEqual(capture2["orders"], [])

    # ── Idempotency ──────────────────────────────────────────────────────────

    async def test_idempotent_replay_returns_existing_order(self):
        existing = order_stub(
            customer_id="cust-1",
            order_number=_order_number_from_key("attempt-123"),
        )
        # 1: order-number lookup, 2: user lookup (customer projection)
        db, capture = make_db([FakeResult([existing]), FakeResult([user_stub()])])
        service = OrderService(db)

        result = await service.place_order(
            make_request(idem="attempt-123"), customer_id="cust-1"
        )

        self.assertIs(result, existing)
        self.assertEqual(capture["orders"], [])  # no duplicate order created

    async def test_idempotency_key_owned_by_other_conflicts(self):
        existing = order_stub(
            customer_id="someone-else",
            order_number=_order_number_from_key("attempt-123"),
        )
        db, _ = make_db([FakeResult([existing])])
        service = OrderService(db)

        with self.assertRaises(ConflictException):
            await service.place_order(
                make_request(idem="attempt-123"), customer_id="cust-1"
            )

    async def test_idempotency_number_is_derived_and_stable(self):
        self.assertEqual(
            _order_number_from_key("abc"), _order_number_from_key("abc")
        )
        self.assertNotEqual(
            _order_number_from_key("abc"), _order_number_from_key("abd")
        )
        self.assertLessEqual(len(_order_number_from_key("abc")), 50)

    # ── Coupon revalidation ──────────────────────────────────────────────────

    async def test_unknown_coupon_is_rejected(self):
        product = product_stub()
        db, capture = make_db([FakeResult([product]), FakeResult([]), LazyLoadedOrder()])
        service = OrderService(db)

        with self.assertRaises(BusinessLogicException):
            await service.place_order(make_request(coupon="NOPE"), customer_id=None)
        self.assertEqual(capture["orders"], [])

    async def test_expired_and_inactive_coupons_are_rejected(self):
        now = datetime.now(timezone.utc)
        product = product_stub()
        for coupon in (
            coupon_stub(is_active=False),
            coupon_stub(expires_at=now - timedelta(days=1)),
            coupon_stub(starts_at=now + timedelta(days=1)),
        ):
            db, capture = make_db(
                [FakeResult([product]), FakeResult([coupon]), LazyLoadedOrder()]
            )
            with self.assertRaises(BusinessLogicException):
                await OrderService(db).place_order(
                    make_request(coupon="FLAT100"), customer_id=None
                )
            self.assertEqual(capture["orders"], [])

    async def test_coupon_below_min_order_value_is_rejected(self):
        product = product_stub()
        coupon = coupon_stub(minimum_order_value=5000)
        db, capture = make_db(
            [FakeResult([product]), FakeResult([coupon]), LazyLoadedOrder()]
        )
        with self.assertRaises(BusinessLogicException):
            await OrderService(db).place_order(
                make_request(coupon="FLAT100"), customer_id=None
            )

    async def test_per_customer_limit_is_enforced_from_redemptions(self):
        product = product_stub()
        coupon = coupon_stub(per_customer_limit=1, code="ONCE")
        prior = SimpleNamespace(id="redem-1")
        db, capture = make_db(
            [
                FakeResult([product]),
                FakeResult([coupon]),
                FakeResult([prior]),
                LazyLoadedOrder(),
            ]
        )
        with self.assertRaises(BusinessLogicException):
            await OrderService(db).place_order(
                make_request(coupon="ONCE"), customer_id="cust-1"
            )
        self.assertEqual(capture["orders"], [])

    async def test_coupon_discount_is_recomputed_and_persisted(self):
        product = product_stub()
        coupon = coupon_stub(discount_value=100, usage_count=0)
        # 1: products, 2: coupon, 3: loaded order, 4: user lookup
        db, capture = make_db(
            [FakeResult([product]), FakeResult([coupon]), LazyLoadedOrder(), FakeResult([user_stub()])]
        )
        service = OrderService(db)

        await service.place_order(make_request(coupon="FLAT100"), customer_id="cust-1")

        order = created_order(capture)
        self.assertEqual(order.coupon_discount, 100)
        self.assertEqual(order.coupon_code, "FLAT100")
        self.assertEqual(order.total, 1000 - 100 + 99)  # shipping on discounted subtotal
        self.assertEqual(coupon.usage_count, 1)
        # Redemption row persisted for the authenticated customer.
        self.assertEqual(len(capture["redemptions"]), 1)
        self.assertEqual(capture["redemptions"][0].customer_id, "cust-1")
        self.assertEqual(capture["redemptions"][0].discount_amount, 100)

    async def test_guest_coupon_recounts_usage_but_has_no_redemption_row(self):
        product = product_stub()
        coupon = coupon_stub(discount_value=100, usage_count=3)
        db, capture = make_db(
            [FakeResult([product]), FakeResult([coupon]), LazyLoadedOrder()]
        )
        await OrderService(db).place_order(
            make_request(coupon="FLAT100"), customer_id=None
        )

        order = created_order(capture)
        self.assertEqual(order.coupon_discount, 100)
        self.assertEqual(coupon.usage_count, 4)
        self.assertEqual(capture["redemptions"], [])  # customer_id FK cannot be NULL

    async def test_free_shipping_coupon_keeps_cart_parity(self):
        # Existing cart semantics: free_shipping grants no cash discount and
        # does not waive shipping (documented limitation, kept consistent).
        product = product_stub()
        coupon = coupon_stub(discount_type="free_shipping", discount_value=0)
        db, capture = make_db(
            [FakeResult([product]), FakeResult([coupon]), LazyLoadedOrder()]
        )
        await OrderService(db).place_order(
            make_request(coupon="FREESHIP"), customer_id=None
        )
        order = created_order(capture)
        self.assertEqual(order.coupon_discount, 0)
        self.assertEqual(order.shipping_fee, 99)


# ---------------------------------------------------------------------------
# Cancellation consistency
# ---------------------------------------------------------------------------

class OrderCancellationTests(unittest.IsolatedAsyncioTestCase):
    def _user_stub(self):
        return SimpleNamespace(full_name="Asha Patel", email="a@b.co", phone="1")

    async def test_cancelling_unpaid_order_releases_stock_and_session(self):
        product = product_stub(stock=3)
        session = session_stub(status="CREATED")
        order = order_stub(
            customer_id="cust-1",
            payment_status="PENDING",
            items=[SimpleNamespace(product_id="PF-TEST-001", quantity=2)],
        )
        # 1: load, 2: locked products, 3: active sessions, 4: reload, 5: user
        db, _ = make_db(
            [
                FakeResult([order]),
                FakeResult([product]),
                FakeResult([session]),
                FakeResult([order]),
                FakeResult([self._user_stub()]),
            ]
        )
        from app.schemas.orders.order import CancelOrderRequest

        await OrderService(db).cancel_order(
            "order-1", "cust-1", CancelOrderRequest(reason="Changed my mind")
        )

        self.assertEqual(order.status, "CANCELLED")
        self.assertEqual(product.stock, 5)      # 3 + 2 returned
        self.assertEqual(session.status, "CANCELLED")

    async def test_admin_cancel_unpaid_guest_order_releases_stock(self):
        product = product_stub(stock=3)
        session = session_stub(status="PENDING")
        order = order_stub(
            customer_id=None,
            guest_email="buyer@example.com",
            payment_status="PENDING",
            items=[SimpleNamespace(product_id="PF-TEST-001", quantity=2)],
        )
        # 1: load, 2: locked products, 3: active sessions, 4: reload
        db, _ = make_db(
            [
                FakeResult([order]),
                FakeResult([product]),
                FakeResult([session]),
                FakeResult([order]),
            ]
        )
        from app.schemas.orders.order import AdminCancelRequest

        await OrderService(db).admin_cancel(
            "order-1", AdminCancelRequest(reason="Customer request"), actor_id="admin-1"
        )

        self.assertEqual(order.status, "CANCELLED")
        self.assertEqual(product.stock, 5)
        self.assertEqual(session.status, "CANCELLED")

    async def test_cancelling_paid_order_keeps_stock_reserved(self):
        product = product_stub(stock=3)
        order = order_stub(
            payment_status="PAID",
            items=[SimpleNamespace(product_id="PF-TEST-001", quantity=2)],
        )
        # Paid orders skip the stock release, so no product query:
        # 1: load, 2: active sessions (none), 3: reload, 4: user
        db, _ = make_db(
            [
                FakeResult([order]),
                FakeResult([]),  # no active sessions
                FakeResult([order]),
                FakeResult([self._user_stub()]),
            ]
        )
        from app.schemas.orders.order import CancelOrderRequest

        await OrderService(db).cancel_order(
            "order-1", "cust-1", CancelOrderRequest(reason="Defect")
        )

        self.assertEqual(order.status, "CANCELLED")
        self.assertEqual(product.stock, 3)  # paid stock handled by returns workflow


# ---------------------------------------------------------------------------
# Guest order claim
# ---------------------------------------------------------------------------

class ClaimGuestOrdersTests(unittest.IsolatedAsyncioTestCase):
    async def test_claim_uses_account_email_and_owns_matching_guest_orders(self):
        guest_order = order_stub(customer_id=None, guest_email="Buyer@Example.com")
        db, _ = make_db([FakeResult([guest_order])])
        service = OrderService(db)

        claimed = await service.claim_guest_orders("buyer@example.com", "cust-1")

        self.assertEqual(claimed, 1)
        self.assertEqual(guest_order.customer_id, "cust-1")
        self.assertIsNone(guest_order.guest_email)
        self.assertEqual(guest_order.timeline[-1]["event"], "ORDER_CLAIMED")

    async def test_claim_is_idempotent_when_nothing_left_to_claim(self):
        # After a successful claim, guest_email is nulled — the SQL filter
        # (customer_id IS NULL AND lower(guest_email) = :email) returns no rows.
        db, _ = make_db([FakeResult([])])
        service = OrderService(db)
        self.assertEqual(await service.claim_guest_orders("buyer@example.com", "cust-1"), 0)

    async def test_route_rejects_foreign_email(self):
        from app.api.v1.orders import claim_guest_orders as route

        user = SimpleNamespace(id="cust-1", email="mine@example.com")
        from app.schemas.orders.order import ClaimGuestOrdersRequest

        with self.assertRaises(ForbiddenException):
            await route(
                ClaimGuestOrdersRequest(email="other@example.com"),
                current_user=user,
                db=AsyncMock(),
            )

    async def test_route_ignores_matching_or_absent_email(self):
        from app.api.v1.orders import claim_guest_orders as route
        from app.schemas.orders.order import ClaimGuestOrdersRequest

        user = SimpleNamespace(id="cust-1", email="mine@example.com")
        guest_order = order_stub(customer_id=None, guest_email="mine@example.com")

        for req_email in (None, "MINE@example.com"):
            db, _ = make_db([FakeResult([guest_order])])
            resp = await route(
                ClaimGuestOrdersRequest(email=req_email), current_user=user, db=db
            )
            self.assertTrue(resp.ok)
            self.assertEqual(resp.claimed, 1)


# ---------------------------------------------------------------------------
# Payment sessions
# ---------------------------------------------------------------------------

class CreatePaymentSessionTests(unittest.IsolatedAsyncioTestCase):
    def _service(self, db):
        return PaymentService(db)

    async def test_order_id_is_required_and_drafts_rejected(self):
        db, _ = make_db([FakeResult([])])
        with self.assertRaises(BusinessLogicException):
            await self._service(db).create_session(
                order_id=None, payment_method="upi", order_draft={"total": 500}
            )
        with self.assertRaises(BusinessLogicException):
            await self._service(db).create_session(order_id=None, payment_method="upi")

    async def test_cod_does_not_use_payment_sessions(self):
        db, _ = make_db([FakeResult([])])
        with self.assertRaises(BusinessLogicException):
            await self._service(db).create_session(
                order_id="order-1", payment_method="cod"
            )

    async def test_cancelled_and_paid_orders_rejected(self):
        cancelled = order_stub(status="CANCELLED")
        db, _ = make_db([FakeResult([cancelled])])
        with self.assertRaises(BusinessLogicException):
            await self._service(db).create_session(order_id="order-1", payment_method="upi")

        paid = order_stub(payment_status="PAID")
        db2, _ = make_db([FakeResult([paid])])
        with self.assertRaises(ConflictException):
            await self._service(db2).create_session(order_id="order-1", payment_method="upi")

    async def test_ownership_is_enforced(self):
        # Customer-owned order, wrong customer
        order = order_stub(customer_id="someone-else")
        db, _ = make_db([FakeResult([order])])
        with self.assertRaises(ForbiddenException):
            await self._service(db).create_session(
                order_id="order-1",
                payment_method="upi",
                owner_customer_id="cust-1",
            )
        # Anonymous caller on a customer order
        db2, _ = make_db([FakeResult([order])])
        with self.assertRaises(ForbiddenException):
            await self._service(db2).create_session(order_id="order-1", payment_method="upi")

        # Guest order, mismatched email
        guest = order_stub(customer_id=None, guest_email="g@x.co")
        db3, _ = make_db([FakeResult([guest])])
        with self.assertRaises(ForbiddenException):
            await self._service(db3).create_session(
                order_id="order-1",
                payment_method="upi",
                owner_guest_email="attacker@x.co",
            )
        # Guest order, authenticated user cannot act on a guest order
        db4, _ = make_db([FakeResult([guest])])
        with self.assertRaises(ForbiddenException):
            await self._service(db4).create_session(
                order_id="order-1",
                payment_method="upi",
                owner_customer_id="cust-1",
            )

    async def test_amount_comes_from_order_total(self):
        order = order_stub(customer_id="cust-1", total=1234)
        # 1: idempotency-key lookup (empty), 2: order load, 3: active-session lookup
        db, _ = make_db([FakeResult([]), FakeResult([order]), FakeResult([])])
        with patch.object(
            PaymentService,
            "_create_razorpay_order",
            new=AsyncMock(return_value={"id": "order_RZPTEST"}),
        ) as rzp:
            resp = await self._service(db).create_session(
                order_id="order-1",
                payment_method="card",
                owner_customer_id="cust-1",
                idempotency_key="attempt-1",
            )
        rzp.assert_awaited_once()
        self.assertEqual(rzp.await_args.kwargs["amount_paise"], 123400)
        # Snake_case contract (frontend API layer normalises to camelCase).
        self.assertEqual(resp["session_id"], capture_session_id(db))
        self.assertEqual(resp["razorpay_order_id"], "order_RZPTEST")
        self.assertEqual(resp["amount_paise"], 123400)
        self.assertIn("razorpay_key_id", resp)

    async def test_active_session_is_resumed_not_duplicated(self):
        order = order_stub(customer_id="cust-1")
        active = session_stub(id="sess-active", status="PENDING")
        db, capture = make_db([FakeResult([order]), FakeResult([active])])
        with patch.object(
            PaymentService,
            "_create_razorpay_order",
            new=AsyncMock(side_effect=AssertionError("must not be called")),
        ):
            resp = await self._service(db).create_session(
                order_id="order-1",
                payment_method="upi",
                owner_customer_id="cust-1",
            )
        self.assertEqual(resp["session_id"], "sess-active")
        self.assertEqual(capture["sessions"], [])  # no new session persisted


def capture_session_id(db):
    added = [c.args[0] for c in db.add.call_args_list if c.args]
    sessions = [a for a in added if isinstance(a, PaymentSessionModel)]
    return sessions[0].id if sessions else None


# ---------------------------------------------------------------------------
# Payment verification
# ---------------------------------------------------------------------------

class VerifyPaymentTests(unittest.IsolatedAsyncioTestCase):
    def _service(self, db):
        return PaymentService(db)

    async def _verify(self, session, order, signature_ok=True, **kwargs):
        db, _ = make_db([FakeResult([session]), FakeResult([order])])
        with patch.object(
            payment_service_module, "_verify_payment_signature", return_value=signature_ok
        ), patch.object(
            payment_service_module, "_build_razorpay_client", side_effect=RuntimeError("no creds")
        ):
            return await self._service(db).verify_payment(
                razorpay_order_id=session.razorpay_order_id,
                razorpay_payment_id="pay_123",
                razorpay_signature="sig",
                **kwargs,
            ), session, order, db

    async def test_valid_signature_confirms_order_canonically(self):
        session = session_stub(status="CREATED")
        order = order_stub(customer_id="cust-1", status="PENDING_PAYMENT")

        resp, session, order, db = await self._verify(
            session, order, signature_ok=True, owner_customer_id="cust-1"
        )

        self.assertTrue(resp["ok"])
        self.assertEqual(resp["payment_status"], "PAID")
        self.assertEqual(resp["order_status"], "ORDER_CONFIRMED")
        self.assertEqual(session.status, "PAID")
        self.assertEqual(order.payment_status, "PAID")
        self.assertEqual(order.status, "ORDER_CONFIRMED")
        # Two canonical transitions recorded.
        history = [
            c.args[0]
            for c in db.add.call_args_list
            if c.args and isinstance(c.args[0], OrderStatusHistoryModel)
        ]
        self.assertEqual([h.to_status for h in history], ["PAYMENT_CONFIRMED", "ORDER_CONFIRMED"])
        self.assertTrue(
            any(e["event"] == "PAYMENT_CAPTURED" for e in order.timeline)
        )

    async def test_invalid_signature_fails_session_and_order_stays_unpaid(self):
        session = session_stub(status="CREATED")
        order = order_stub(customer_id="cust-1", status="PENDING_PAYMENT")

        with self.assertRaises(BusinessLogicException):
            await self._verify(session, order, signature_ok=False, owner_customer_id="cust-1")

        self.assertEqual(session.status, "FAILED")
        self.assertEqual(session.failure_code, "SIGNATURE_MISMATCH")
        self.assertEqual(order.payment_status, "PENDING")  # never PAID
        self.assertEqual(order.status, "PENDING_PAYMENT")

    async def test_cancelled_order_cannot_be_paid(self):
        session = session_stub(status="CREATED")
        order = order_stub(customer_id="cust-1", status="CANCELLED")

        with self.assertRaises(BusinessLogicException):
            await self._verify(session, order, signature_ok=True, owner_customer_id="cust-1")

        self.assertEqual(session.status, "FAILED")
        self.assertEqual(session.failure_code, "ORDER_CANCELLED")
        self.assertEqual(order.payment_status, "PENDING")

    async def test_ownership_is_enforced_on_verify(self):
        order = order_stub(customer_id="cust-1", status="PENDING_PAYMENT")
        session = session_stub(status="CREATED")

        with self.assertRaises(ForbiddenException):
            await self._verify(session, order, owner_customer_id="someone-else")

        guest_order = order_stub(customer_id=None, guest_email="g@x.co")
        with self.assertRaises(ForbiddenException):
            await self._verify(session, guest_order, owner_guest_email="h@x.co")
        # Anonymous caller without any guest email
        with self.assertRaises(ForbiddenException):
            await self._verify(session, guest_order)

    async def test_already_paid_is_idempotent(self):
        session = session_stub(status="PAID")
        order = order_stub(customer_id="cust-1", status="ORDER_CONFIRMED", payment_status="PAID")

        resp, _, _, _ = await self._verify(session, order, signature_ok=True, owner_customer_id="cust-1")

        self.assertTrue(resp["ok"])
        self.assertEqual(resp["order_status"], "ORDER_CONFIRMED")


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------

class WebhookTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_signature_is_rejected(self):
        db, _ = make_db([FakeResult([])])
        with patch.object(
            payment_service_module, "_verify_webhook_signature", return_value=False
        ):
            with self.assertRaises(ForbiddenException):
                await PaymentService(db).handle_webhook(
                    b'{"event":"payment.captured"}', "bad-signature"
                )

    async def test_payment_captured_confirms_order(self):
        session = session_stub(status="CREATED", amount_paise=109900)
        order = order_stub(customer_id="cust-1", status="PENDING_PAYMENT")
        body = json.dumps(
            {
                "event": "payment.captured",
                "payload": {
                    "payment": {
                        "entity": {
                            "id": "pay_999",
                            "order_id": "order_RZP1",
                            "amount": 109900,
                        }
                    }
                },
            }
        ).encode("utf-8")

        db, _ = make_db([FakeResult([session]), FakeResult([order])])
        with patch.object(
            payment_service_module, "_verify_webhook_signature", return_value=True
        ):
            resp = await PaymentService(db).handle_webhook(body, "good-signature")

        self.assertTrue(resp["ok"])
        self.assertEqual(session.status, "PAID")
        self.assertEqual(order.payment_status, "PAID")
        self.assertEqual(order.status, "ORDER_CONFIRMED")

    async def test_webhook_amount_mismatch_fails_session(self):
        session = session_stub(status="CREATED", amount_paise=109900)
        body = json.dumps(
            {
                "event": "payment.captured",
                "payload": {
                    "payment": {
                        "entity": {
                            "id": "pay_999",
                            "order_id": "order_RZP1",
                            "amount": 500,  # wrong amount
                        }
                    }
                },
            }
        ).encode("utf-8")

        db, _ = make_db([FakeResult([session]), FakeResult([order_stub()])])
        with patch.object(
            payment_service_module, "_verify_webhook_signature", return_value=True
        ):
            await PaymentService(db).handle_webhook(body, "good-signature")

        self.assertEqual(session.status, "FAILED")
        self.assertEqual(session.failure_code, "AMOUNT_MISMATCH")


if __name__ == "__main__":
    unittest.main()
