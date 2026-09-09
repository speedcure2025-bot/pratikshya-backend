"""
Phase 3 unit tests — order read model, tracking, invoice, returns and
admin order reads.

These are focused regression checks for the honesty guarantees introduced
in Phase 3 (no fabricated tracking events, carriers, delivery dates or
invoice numbers), plus the ownership and eligibility rules the frontend
now mirrors.

Covers (no production DB, no network):
  - get_tracking: events are persisted status-history rows only, ordered
    by their stored timestamps; no synthesised courier scans; shipment
    identity is passed through verbatim or reported unavailable;
    ownership is enforced.
  - TrackingResponse DTO: `carrier_events_available` is structurally
    false; no `origin` field survives.
  - get_invoice: reports `available` honestly and never a document URL.
  - list_orders: allow-listed sort, page metadata, ownership scoping.
  - create_return: DELIVERED-only, return-window and per-line quantity
    rules; ownership.
  - OrderResponse.returns: returns travel with the order (admin desks).
  - admin reads: customer identity attached; eager-load options cover
    `returns` so nothing lazy-loads under async.

Pattern follows tests/unit/test_phase2_checkout.py (IsolatedAsyncioTestCase
+ AsyncMock db + SimpleNamespace stubs).
"""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app.core.exceptions import BusinessLogicException, ForbiddenException
from app.schemas.orders.order import (
    CreateReturnRequest,
    InvoiceResponse,
    OrderListResponse,
    TrackingEvent,
    TrackingResponse,
)
from app.services.orders.order_service import (
    OrderService,
    _list_sort_clause,
    _order_load_options,
)


# ---------------------------------------------------------------------------
# Fakes / stubs
# ---------------------------------------------------------------------------

UTC = timezone.utc
T0 = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)


class FakeScalars:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values

    def first(self):
        return self.values[0] if self.values else None

    def one_or_none(self):
        return self.values[0] if self.values else None


class FakeResult:
    def __init__(self, values, scalar=None):
        self.values = values
        self._scalar = scalar

    def scalars(self):
        return FakeScalars(self.values)

    def scalar_one(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self.values[0] if self.values else None


def make_db(results):
    """`execute` pops `results` in call order; the last result repeats."""
    db = AsyncMock()
    state = {"i": 0}

    def _execute(stmt):
        r = results[min(state["i"], len(results) - 1)]
        state["i"] += 1
        return r

    db.execute = AsyncMock(side_effect=_execute)
    db.add = Mock()
    db.flush = AsyncMock()
    return db


def history_stub(to_status, created_at, from_status=None, note=None, actor_name=None):
    return SimpleNamespace(
        id=f"h-{to_status}",
        to_status=to_status,
        from_status=from_status,
        created_at=created_at,
        note=note,
        actor_name=actor_name,
        actor_id=None,
    )


def item_stub(**overrides):
    base = dict(
        id="line-1",
        product_id="PF-1",
        product_name="Test Saree",
        product_image="/images/test.webp",
        sku="SKU-1",
        color="Ivory",
        size=None,
        unit_price=1000,
        original_price=1100,
        quantity=2,
        line_total=2000,
        returned_quantity=0,
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
        status="DELIVERED",
        payment_status="PAID",
        payment_method="upi",
        delivery_method="standard",
        shipping_address={"fullName": "Asha Patel", "city": "Kolkata"},
        subtotal=2000,
        total=2000,
        carrier=None,
        tracking_number=None,
        estimated_delivery=None,
        dispatched_at=None,
        delivered_at=None,
        cancelled_at=None,
        cancellation_reason=None,
        invoice_number=None,
        invoice_issued_at=None,
        timeline=[],
        internal_notes=[],
        items=[],
        status_history=[],
        returns=[],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# Tracking — the core honesty guarantee
# ---------------------------------------------------------------------------

class TrackingHonestyTests(unittest.IsolatedAsyncioTestCase):
    """`get_tracking` may only report what the atelier actually recorded."""

    def _service(self, order):
        return OrderService(make_db([FakeResult([order])]))

    async def test_events_are_persisted_status_history_only(self):
        """Every event maps 1:1 to a stored status-history row."""
        order = order_stub(
            status="SHIPPED",
            status_history=[
                history_stub("ORDER_CONFIRMED", T0),
                history_stub("PROCESSING", T0 + timedelta(hours=3), from_status="ORDER_CONFIRMED"),
                history_stub("SHIPPED", T0 + timedelta(days=1), from_status="PROCESSING"),
            ],
        )
        data = await self._service(order).get_tracking("order-1", "cust-1")

        self.assertEqual(len(data["events"]), 3)
        self.assertEqual(
            [e["status"] for e in data["events"]],
            ["ORDER_CONFIRMED", "PROCESSING", "SHIPPED"],
        )
        # Stored timestamps are returned verbatim — never "now".
        self.assertEqual(data["events"][0]["timestamp"], T0)
        self.assertEqual(data["events"][2]["timestamp"], T0 + timedelta(days=1))
        for event in data["events"]:
            self.assertEqual(event["source"], "STATUS_HISTORY")

    async def test_events_are_sorted_by_stored_timestamp(self):
        order = order_stub(
            status_history=[
                history_stub("SHIPPED", T0 + timedelta(days=1)),
                history_stub("ORDER_CONFIRMED", T0),
            ]
        )
        data = await self._service(order).get_tracking("order-1", "cust-1")
        self.assertEqual(
            [e["status"] for e in data["events"]], ["ORDER_CONFIRMED", "SHIPPED"]
        )

    async def test_no_events_are_synthesised_for_a_shipped_order(self):
        """
        Regression: the old implementation invented "dispatched from
        Bhubaneswar" / "Out for delivery" / "Delivered" events (with
        `now()` timestamps) purely from the order status.
        """
        order = order_stub(status="DELIVERED", status_history=[])
        data = await self._service(order).get_tracking("order-1", "cust-1")

        self.assertEqual(data["events"], [])
        self.assertNotIn("origin", data)
        serialised = str(data)
        self.assertNotIn("Bhubaneswar", serialised)
        self.assertNotIn("Out for delivery", serialised)

    async def test_carrier_events_are_never_available(self):
        """No courier integration exists anywhere in the system."""
        order = order_stub(carrier="Delhivery", tracking_number="DL123")
        data = await self._service(order).get_tracking("order-1", "cust-1")
        self.assertFalse(data["carrier_events_available"])

    async def test_shipment_identity_passed_through_or_reported_unavailable(self):
        undispatched = await self._service(order_stub()).get_tracking("order-1", "cust-1")
        self.assertIsNone(undispatched["carrier"])
        self.assertIsNone(undispatched["tracking_number"])
        self.assertIsNone(undispatched["estimated_delivery"])
        self.assertFalse(undispatched["carrier_tracking_available"])

        eta = datetime(2026, 8, 28, tzinfo=UTC)
        dispatched = order_stub(
            carrier="Blue Dart", tracking_number="BD-9", estimated_delivery=eta
        )
        data = await self._service(dispatched).get_tracking("order-1", "cust-1")
        self.assertEqual(data["carrier"], "Blue Dart")
        self.assertEqual(data["tracking_number"], "BD-9")
        self.assertEqual(data["estimated_delivery"], eta)
        self.assertTrue(data["carrier_tracking_available"])

    async def test_order_and_payment_status_reported_separately(self):
        order = order_stub(status="SHIPPED", payment_status="PAID")
        data = await self._service(order).get_tracking("order-1", "cust-1")
        self.assertEqual(data["order_status"], "SHIPPED")
        self.assertEqual(data["payment_status"], "PAID")

    async def test_tracking_requires_ownership(self):
        service = self._service(order_stub(customer_id="someone-else"))
        with self.assertRaises(ForbiddenException):
            await service.get_tracking("order-1", "cust-1")


class TrackingSchemaTests(unittest.TestCase):
    """The DTO itself must not be able to express fabricated data."""

    def test_tracking_response_defaults_are_honest(self):
        response = TrackingResponse(
            order_id="order-1", order_status="PLACED", payment_status="PENDING"
        )
        self.assertFalse(response.carrier_events_available)
        self.assertFalse(response.carrier_tracking_available)
        self.assertEqual(response.events, [])
        self.assertIsNone(response.carrier)
        self.assertIsNone(response.tracking_number)
        self.assertIsNone(response.estimated_delivery)

    def test_tracking_response_has_no_origin_field(self):
        """Regression: a hard-coded fulfilment origin used to be returned."""
        self.assertNotIn("origin", TrackingResponse.model_fields)

    def test_tracking_event_records_its_source(self):
        event = TrackingEvent(status="SHIPPED", timestamp=T0)
        self.assertEqual(event.source, "STATUS_HISTORY")
        self.assertIsNone(event.note)


# ---------------------------------------------------------------------------
# Invoice
# ---------------------------------------------------------------------------

class InvoiceTests(unittest.IsolatedAsyncioTestCase):

    async def test_invoice_unavailable_when_never_issued(self):
        db = make_db([FakeResult([order_stub()])])
        data = await OrderService(db).get_invoice("order-1")
        self.assertIsNone(data["invoice_number"])
        self.assertFalse(data["available"])
        self.assertFalse(data["document_available"])

    async def test_invoice_available_when_issued(self):
        issued = datetime(2026, 8, 21, tzinfo=UTC)
        db = make_db([FakeResult([order_stub(invoice_number="INV-9", invoice_issued_at=issued)])])
        data = await OrderService(db).get_invoice("order-1")
        self.assertEqual(data["invoice_number"], "INV-9")
        self.assertEqual(data["issued_at"], issued)
        self.assertTrue(data["available"])
        # Still no document: no PDF pipeline exists.
        self.assertFalse(data["document_available"])

    def test_invoice_response_never_exposes_a_download_url(self):
        response = InvoiceResponse(order_id="order-1")
        self.assertFalse(response.available)
        self.assertFalse(response.document_available)
        self.assertNotIn("download_url", InvoiceResponse.model_fields)
        self.assertNotIn("url", InvoiceResponse.model_fields)


# ---------------------------------------------------------------------------
# Order list
# ---------------------------------------------------------------------------

class OrderListTests(unittest.IsolatedAsyncioTestCase):

    async def test_list_returns_page_metadata(self):
        order = order_stub()
        db = make_db([
            FakeResult([], scalar=7),   # count
            FakeResult([order]),        # page
            FakeResult([]),             # customer lookup
        ])
        result = await OrderService(db).list_orders("cust-1", page=2, page_size=3)
        self.assertEqual(result["total"], 7)
        self.assertEqual(result["page"], 2)
        self.assertEqual(result["page_size"], 3)
        self.assertEqual(len(result["orders"]), 1)

    def test_sort_is_allow_listed(self):
        newest = str(_list_sort_clause("newest"))
        oldest = str(_list_sort_clause("oldest"))
        self.assertIn("DESC", newest.upper())
        self.assertIn("ASC", oldest.upper())
        # Anything unrecognised falls back to newest — never interpolated.
        self.assertEqual(str(_list_sort_clause("created_at; DROP TABLE")), newest)
        self.assertEqual(str(_list_sort_clause(None)), newest)

    def test_list_response_carries_page_metadata(self):
        response = OrderListResponse(orders=[], total=0)
        self.assertEqual(response.page, 1)
        self.assertEqual(response.page_size, 20)


# ---------------------------------------------------------------------------
# Returns
# ---------------------------------------------------------------------------

class ReturnRuleTests(unittest.IsolatedAsyncioTestCase):

    def _request(self, quantity=1, line_id="line-1"):
        return CreateReturnRequest(
            items=[{"lineId": line_id, "quantity": quantity, "reason": "damaged"}],
            pickupMethod="SCHEDULED_PICKUP",
        )

    async def test_returns_require_ownership(self):
        db = make_db([FakeResult([order_stub(customer_id="other")])])
        with self.assertRaises(ForbiddenException):
            await OrderService(db).create_return("order-1", "cust-1", self._request())

    async def test_returns_require_delivered_status(self):
        db = make_db([FakeResult([order_stub(status="SHIPPED")])])
        with self.assertRaises(BusinessLogicException):
            await OrderService(db).create_return("order-1", "cust-1", self._request())

    async def test_return_window_is_enforced(self):
        stale = order_stub(
            delivered_at=datetime.now(UTC) - timedelta(days=30),
            items=[item_stub()],
        )
        db = make_db([FakeResult([stale])])
        with self.assertRaises(BusinessLogicException) as ctx:
            await OrderService(db).create_return("order-1", "cust-1", self._request())
        self.assertIn("window", str(ctx.exception).lower())

    async def test_cannot_return_more_than_remains(self):
        order = order_stub(
            delivered_at=datetime.now(UTC) - timedelta(days=1),
            items=[item_stub(quantity=2, returned_quantity=1)],
        )
        db = make_db([FakeResult([order])])
        with self.assertRaises(BusinessLogicException) as ctx:
            await OrderService(db).create_return("order-1", "cust-1", self._request(quantity=2))
        self.assertIn("returnable", str(ctx.exception).lower())

    async def test_unknown_line_is_rejected(self):
        order = order_stub(
            delivered_at=datetime.now(UTC) - timedelta(days=1),
            items=[item_stub()],
        )
        db = make_db([FakeResult([order])])
        with self.assertRaises(BusinessLogicException):
            await OrderService(db).create_return(
                "order-1", "cust-1", self._request(line_id="line-does-not-exist")
            )


# ---------------------------------------------------------------------------
# Admin reads
# ---------------------------------------------------------------------------

class AdminReadTests(unittest.IsolatedAsyncioTestCase):

    async def test_admin_list_attaches_customer_identity(self):
        order = order_stub()
        user = SimpleNamespace(
            id="cust-1",
            first_name="Asha",
            last_name="Patel",
            full_name="Asha Patel",
            email="asha@example.com",
            phone="9876543210",
        )
        db = make_db([
            FakeResult([], scalar=1),   # count
            FakeResult([order]),        # page
            FakeResult([user]),         # customer lookup
        ])
        result = await OrderService(db).admin_list_orders(page=1, page_size=20)
        self.assertEqual(result["orders"][0].customer["email"], "asha@example.com")

    def test_order_load_options_cover_returns(self):
        """
        `returns` must be eager-loaded alongside items and status history:
        a lazy load inside an async request raises rather than silently
        returning [].
        """
        rendered = " ".join(
            str(entry)
            for option in _order_load_options()
            for element in option.context
            for entry in element.path
        )
        self.assertIn("OrderModel.items", rendered)
        self.assertIn("OrderModel.status_history", rendered)
        self.assertIn("OrderModel.returns", rendered)
        # Return lines too — the admin returns desk reads them.
        self.assertIn("ReturnOrderModel.items", rendered)


if __name__ == "__main__":
    unittest.main()
