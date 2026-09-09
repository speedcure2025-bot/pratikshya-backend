"""
Phase 4 unit tests — customer cart, wishlist and account/address consistency.

Focused regression checks for the Phase 4 guarantees (no production DB, no
network):

  - Cart totals honour the SAME shipping rules as the Phase 2 order
    boundary (express ₹199 never free, standard free at/above ₹5,000, COD
    fee ₹49) so the cart display and a placed order can never disagree.
  - Cart line identity is the backend hash of the case-insensitive
    (productId, colour, size) triple.
  - Wishlist adds validate product existence/visibility at the application
    boundary (the table has no FK to the catalogue — schema unchanged).
  - Wishlist mutation responses reflect the mutation (no stale loaded
    relationship), adds are idempotent, and reads keep orphan product ids
    verbatim so clients can show an honest unavailable state.
  - Customer DTO aliases round-trip (camelCase in/out) and address
    validation matches the documented frontend rules.
  - Session summaries cannot identify the calling session (documented
    backend gap: every session is `isCurrent: false` and revoke-others ends
    ALL sessions).

Pattern follows tests/unit/test_phase2_checkout.py (IsolatedAsyncioTestCase
+ AsyncMock db + fakes).
"""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from pydantic import ValidationError

from app.core.exceptions import NotFoundException
from app.models.commerce.cart import CartModel
from app.models.commerce.cart_item import CartItemModel
from app.models.commerce.wishlist import WishlistModel
from app.models.commerce.wishlist_item import WishlistItemModel
from app.schemas.customer.address import AddressCreate
from app.schemas.customer.customer import ProfileUpdate, SessionSummary
from app.services.commerce.cart_service import CartService, _cart_line_id
from app.services.commerce.wishlist_service import WishlistService
from app.services.customer.customer_service import CustomerService
from app.services.orders.order_service import _compute_shipping


# ---------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------

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

    def all(self):
        return self.values

    def scalar_one(self):
        return self.values[0]


def make_db(results):
    """`execute` pops `results` in call order; the last result repeats."""
    db = AsyncMock()
    state = {"i": 0}
    db.captured_statements = []

    def _execute(stmt):
        db.captured_statements.append(stmt)
        r = results[min(state["i"], len(results) - 1)]
        state["i"] += 1
        return r

    db.execute = AsyncMock(side_effect=_execute)
    db.add = Mock()
    db.flush = AsyncMock()
    db.delete = AsyncMock()
    return db


def product_stub(pid="PF-PRD-1", price=3000, stock=5, status="PUBLISHED",
                 published=True, availability="in-stock"):
    return SimpleNamespace(
        id=pid,
        name=f"Piece {pid}",
        slug=pid.lower(),
        image="/images/p.webp",
        price=price,
        original_price=None,
        stock=stock,
        status=status,
        published=published,
        availability=availability,
        pricing=None,
        primary_color="",
        colors=[],
        sizes=[],
    )


def cart_with_item(pid="PF-PRD-1", quantity=2, color="Red", size="M"):
    cart = CartModel(customer_id="u1")
    cart.items.append(
        CartItemModel(product_id=pid, color=color, size=size, quantity=quantity,
                      added_at=datetime.now(timezone.utc))
    )
    return cart


# ---------------------------------------------------------------------
# Cart totals — method-dependent, order-boundary parity
# ---------------------------------------------------------------------

class CartTotalsTests(unittest.IsolatedAsyncioTestCase):
    async def _totals(self, price, quantity, delivery_method="standard",
                      payment_method="online"):
        product = product_stub(price=price, stock=99)
        cart = cart_with_item(quantity=quantity)
        db = make_db([FakeResult([cart]), FakeResult([product]), FakeResult([product])])
        service = CartService(db)
        return await service.get_totals(
            "u1", delivery_method=delivery_method, payment_method=payment_method
        )

    async def test_standard_shipping_below_threshold(self):
        t = await self._totals(price=2000, quantity=1)  # ₹2,000
        self.assertEqual(t.shipping, 99)
        self.assertEqual(t.cod_fee, 0)
        self.assertEqual(t.total, 2099)

    async def test_standard_shipping_free_at_threshold(self):
        t = await self._totals(price=2500, quantity=2)  # ₹5,000
        self.assertEqual(t.shipping, 0)
        self.assertEqual(t.total, 5000)

    async def test_express_is_never_free_even_above_threshold(self):
        t = await self._totals(price=6000, quantity=1, delivery_method="express")
        self.assertEqual(t.shipping, 199)
        self.assertEqual(t.total, 6199)

    async def test_cod_fee_applies_only_for_cod(self):
        online = await self._totals(price=2000, quantity=1, payment_method="online")
        cod = await self._totals(price=2000, quantity=1, payment_method="cod")
        self.assertEqual(online.cod_fee, 0)
        self.assertEqual(cod.cod_fee, 49)
        self.assertEqual(cod.total, online.total + 49)

    async def test_shipping_matches_the_order_boundary_rule(self):
        # Parity with services/orders/order_service._compute_shipping for the
        # values the storefront can produce — the cart display can never
        # quote a shipping figure the order boundary would not charge.
        for subtotal, method in [(2000, "standard"), (5000, "standard"),
                                 (6000, "standard"), (2000, "express"),
                                 (6000, "express")]:
            t = await self._totals(price=subtotal, quantity=1, delivery_method=method)
            self.assertEqual(
                t.shipping,
                _compute_shipping(subtotal, method),
                f"{method} @ ₹{subtotal}",
            )


# ---------------------------------------------------------------------
# Cart line identity
# ---------------------------------------------------------------------

class CartLineIdentityTests(unittest.TestCase):
    def test_line_id_is_case_insensitive_on_colour_and_size(self):
        self.assertEqual(
            _cart_line_id("PF-PRD-1", "Red", "M"),
            _cart_line_id("PF-PRD-1", "red", "m"),
        )

    def test_line_id_separates_selections(self):
        self.assertNotEqual(
            _cart_line_id("PF-PRD-1", "Red", "M"),
            _cart_line_id("PF-PRD-1", "Red", "L"),
        )
        self.assertNotEqual(
            _cart_line_id("PF-PRD-1", None, None),
            _cart_line_id("PF-PRD-1", "Red", None),
        )

    def test_line_id_is_a_deterministic_sixteen_char_hash(self):
        a, b = _cart_line_id("PF-PRD-1", "Red", "M"), _cart_line_id("PF-PRD-1", "Red", "M")
        self.assertEqual(a, b)
        self.assertEqual(len(a), 16)


# ---------------------------------------------------------------------
# Wishlist — validation + honest mutation responses
# ---------------------------------------------------------------------

class WishlistServiceTests(unittest.IsolatedAsyncioTestCase):
    async def _service(self, results):
        db = make_db(results)
        return WishlistService(db), db

    async def test_add_rejects_unknown_product(self):
        service, _ = await self._service([
            FakeResult([WishlistModel(customer_id="u1")]),   # wishlist
            FakeResult([]),                                  # product lookup: none
        ])
        with self.assertRaises(NotFoundException):
            await service.add_product("u1", "PF-GONE")

    async def test_add_rejects_unpublished_product(self):
        wishlist = WishlistModel(customer_id="u1")
        service, _ = await self._service([
            FakeResult([wishlist]),
            FakeResult([product_stub(status="DRAFT")]),
        ])
        with self.assertRaises(NotFoundException):
            await service.add_product("u1", "PF-PRD-1")

    async def test_add_rejects_unlisted_product(self):
        wishlist = WishlistModel(customer_id="u1")
        service, _ = await self._service([
            FakeResult([wishlist]),
            FakeResult([product_stub(published=False)]),
        ])
        with self.assertRaises(NotFoundException):
            await service.add_product("u1", "PF-PRD-1")

    async def test_add_returns_the_new_item_in_the_response(self):
        # Regression for the stale-loaded-relationship defect: the mutation
        # response must include the just-added product id.
        wishlist = WishlistModel(customer_id="u1")
        service, db = await self._service([
            FakeResult([wishlist]),
            FakeResult([product_stub()]),
        ])
        response = await service.add_product("u1", "PF-PRD-1")
        self.assertEqual(response["items"], ["PF-PRD-1"])
        self.assertEqual(response["count"], 1)
        self.assertTrue(db.add.called)

    async def test_add_is_idempotent_for_an_existing_product(self):
        wishlist = WishlistModel(customer_id="u1")
        wishlist.items.append(WishlistItemModel(product_id="PF-PRD-1"))
        service, db = await self._service([FakeResult([wishlist])])
        response = await service.add_product("u1", "PF-PRD-1")
        self.assertEqual(response["items"], ["PF-PRD-1"])
        self.assertFalse(db.add.called, "no second row for an already-saved product")

    async def test_remove_reflects_the_removal_in_the_response(self):
        wishlist = WishlistModel(customer_id="u1")
        wishlist.items.append(WishlistItemModel(product_id="PF-PRD-1"))
        wishlist.items.append(WishlistItemModel(product_id="PF-PRD-2"))
        service, db = await self._service([FakeResult([wishlist])])
        response = await service.remove_product("u1", "PF-PRD-1")
        self.assertEqual(response["items"], ["PF-PRD-2"])
        self.assertTrue(db.delete.called)

    async def test_remove_is_a_no_op_for_an_absent_product(self):
        wishlist = WishlistModel(customer_id="u1")
        service, db = await self._service([FakeResult([wishlist])])
        response = await service.remove_product("u1", "PF-NOT-THERE")
        self.assertEqual(response["items"], [])
        self.assertFalse(db.delete.called)

    async def test_toggle_adds_when_absent_and_removes_when_present(self):
        wishlist = WishlistModel(customer_id="u1")
        service, _ = await self._service([
            FakeResult([wishlist]),          # toggle's own read
            FakeResult([wishlist]),          # add_product's read
            FakeResult([product_stub()]),    # product validation
        ])
        added = await service.toggle_product("u1", "PF-PRD-1")
        self.assertEqual(added["items"], ["PF-PRD-1"])

        wishlist2 = WishlistModel(customer_id="u1")
        wishlist2.items.append(WishlistItemModel(product_id="PF-PRD-1"))
        service2, _ = await self._service([FakeResult([wishlist2])])
        removed = await service2.toggle_product("u1", "PF-PRD-1")
        self.assertEqual(removed["items"], [])

    async def test_reads_keep_orphan_product_ids_verbatim(self):
        # commerce_wishlist_item has no FK to the catalogue (existing schema);
        # ids saved before a product was removed must stay visible so the
        # client can show an honest unavailable state — never silently dropped.
        wishlist = WishlistModel(customer_id="u1")
        wishlist.items.append(WishlistItemModel(product_id="PF-ORPHAN"))
        service, _ = await self._service([FakeResult([wishlist])])
        response = await service.get_wishlist("u1")
        self.assertEqual(response["items"], ["PF-ORPHAN"])
        self.assertEqual(response["count"], 1)


# ---------------------------------------------------------------------
# Customer DTOs & session summaries
# ---------------------------------------------------------------------

class CustomerSchemaTests(unittest.TestCase):
    def test_profile_update_accepts_camel_aliases(self):
        data = ProfileUpdate.model_validate({
            "firstName": "Aditi",
            "lastName": "Rao",
            "dateOfBirth": "1995-04-02",
        })
        self.assertEqual(data.first_name, "Aditi")
        self.assertEqual(data.last_name, "Rao")
        self.assertEqual(data.date_of_birth.isoformat(), "1995-04-02")

    def test_profile_update_rejects_an_invalid_email(self):
        with self.assertRaises(ValidationError):
            ProfileUpdate.model_validate({"email": "not-an-email"})

    def test_session_summary_serialises_camel_aliases(self):
        summary = SessionSummary(
            id="s1",
            ip_address="10.0.0.1",
            user_agent="Mozilla/5.0",
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc),
            is_current=False,
        )
        dumped = summary.model_dump(by_alias=True)
        self.assertIn("isCurrent", dumped)
        self.assertIn("ipAddress", dumped)
        self.assertIn("userAgent", dumped)
        self.assertFalse(dumped["isCurrent"])


class AddressSchemaTests(unittest.TestCase):
    def test_address_create_accepts_plus91_phone_and_aliases(self):
        address = AddressCreate.model_validate({
            "fullName": "Aditi Rao",
            "phone": "+919876543210",
            "addressLine": "Flat 402, Lotus Residency",
            "landmark": "Near the club",
            "city": "Bengaluru",
            "state": "Karnataka",
            "pincode": "560038",
            "type": "Home",
            "isDefault": True,
        })
        self.assertEqual(address.full_name, "Aditi Rao")
        self.assertTrue(address.is_default)
        self.assertEqual(address.address_type, "Home")

    def test_address_create_rejects_an_invalid_pincode(self):
        with self.assertRaises(ValidationError):
            AddressCreate.model_validate({
                "fullName": "A", "phone": "9876543210",
                "addressLine": "x", "city": "y", "state": "z",
                "pincode": "12345",
            })

    def test_address_create_rejects_an_invalid_phone(self):
        with self.assertRaises(ValidationError):
            AddressCreate.model_validate({
                "fullName": "A", "phone": "12345",
                "addressLine": "x", "city": "y", "state": "z",
                "pincode": "560038",
            })

    def test_address_validation_matches_the_frontend_rules(self):
        # The frontend validators accept the same shapes: 10 digits starting
        # 6-9, optional +91/0 prefix; pincodes 1-9 followed by 5 digits.
        for phone in ["9876543210", "+919876543210", "09876543210"]:
            AddressCreate.model_validate({
                "fullName": "A", "phone": phone, "addressLine": "x",
                "city": "y", "state": "z", "pincode": "751001",
            })
        for bad in ["5876543210", "987654321", "+9198765432199"]:
            with self.assertRaises(ValidationError, msg=bad):
                AddressCreate.model_validate({
                    "fullName": "A", "phone": bad, "addressLine": "x",
                    "city": "y", "state": "z", "pincode": "751001",
                })


# ---------------------------------------------------------------------
# Session identification — documented backend gap
# ---------------------------------------------------------------------

class SessionIdentificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_me_without_a_session_identifier_marks_nothing_current(self):
        # Documented gap: /customers/me has no way to know which session is
        # calling, so every summary is isCurrent=false. The frontend renders
        # the recorded values only — it never fabricates a "current device".
        now = datetime.now(timezone.utc)
        profile = SimpleNamespace(
            first_name="Aditi", last_name="Rao", date_of_birth=None, avatar=None,
            loyalty_tier="BRONZE", loyalty_points=0, addresses=[],
            preferences=SimpleNamespace(
                email_notifications=True, sms_notifications=True,
                promotional_updates=True, order_updates=True,
                styling_invitations=True,
            ),
        )
        user = SimpleNamespace(
            id="u1", email="a@example.com", phone=None,
            created_at=now, customer_profile=profile,
        )
        sessions = [
            SimpleNamespace(id=f"s{i}", ip_address="10.0.0.1",
                            user_agent="Mozilla/5.0",
                            created_at=now, expires_at=now, is_revoked=False)
            for i in range(2)
        ]
        db = make_db([FakeResult([user]), FakeResult(sessions)])
        service = CustomerService(db)

        _, _, _, summaries = await service.get_me("u1")

        self.assertEqual(len(summaries), 2)
        self.assertTrue(all(s.is_current is False for s in summaries),
                        "no session can be identified as current — the documented gap")

    async def test_revoke_other_sessions_without_an_identifier_ends_all_sessions(self):
        # Documented gap: the route cannot identify the calling session, so
        # revoke-others revokes EVERY session. The frontend states this
        # honestly ("you will sign in again") instead of pretending only
        # other devices were signed out.
        sessions = [
            SimpleNamespace(id=f"s{i}", user_id="u1", is_revoked=False,
                            expires_at=datetime.now(timezone.utc))
            for i in range(3)
        ]
        db = make_db([FakeResult(sessions)])
        db.commit = AsyncMock()
        service = CustomerService(db)
        revoked = await service.revoke_other_sessions("u1")
        self.assertEqual(revoked, 3)
        self.assertTrue(all(s.is_revoked for s in sessions))


# ---------------------------------------------------------------------
# Admin customer aggregates (order count / lifetime spend are REAL joins,
# not hardcoded zeros)
# ---------------------------------------------------------------------

def admin_user_stub(uid, profile=True):
    return SimpleNamespace(
        id=uid,
        email=f"{uid}@example.com",
        phone=None,
        status="ACTIVE",
        created_at=datetime.now(timezone.utc),
        customer_profile=(
            SimpleNamespace(
                first_name="Ada",
                last_name="Lovelace",
                loyalty_tier="BRONZE",
                loyalty_points=10,
                addresses=[],
            )
            if profile
            else None
        ),
    )


class AdminCustomerAggregatesTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_customers_uses_real_order_aggregates(self):
        # execute order: count → users page → grouped order aggregates
        db = make_db([
            FakeResult([2]),                                   # total count
            FakeResult([admin_user_stub("cu-1"), admin_user_stub("cu-2", profile=False)]),
            FakeResult([("cu-1", 3, 12500.0), ("cu-2", 1, 0.0)]),
        ])
        svc = CustomerService(db)
        result, total = await svc.list_customers(page=1, page_size=20)
        self.assertEqual(total, 2)
        by_id = {c.id: c for c in result}
        self.assertEqual(by_id["cu-1"].order_count, 3)
        self.assertEqual(by_id["cu-1"].lifetime_spend, 12500.0)
        self.assertEqual(by_id["cu-2"].order_count, 1)
        self.assertEqual(by_id["cu-2"].lifetime_spend, 0.0)

    async def test_list_customers_customer_without_orders_defaults_zero(self):
        db = make_db([
            FakeResult([1]),
            FakeResult([admin_user_stub("cu-1")]),
            FakeResult([]),                                    # no aggregate rows
        ])
        svc = CustomerService(db)
        result, _ = await svc.list_customers(page=1, page_size=20)
        self.assertEqual(result[0].order_count, 0)
        self.assertEqual(result[0].lifetime_spend, 0.0)

    async def test_aggregate_sql_counts_all_orders_but_sums_revenue_statuses_only(self):
        # execute order: users page (detail path) → aggregates
        db = make_db([
            FakeResult([admin_user_stub("cu-1")]),
            FakeResult([("cu-1", 4, 9999.0)]),
        ])
        svc = CustomerService(db)
        detail = await svc.get_customer_detail("cu-1")
        self.assertEqual(detail.order_count, 4)
        self.assertEqual(detail.lifetime_spend, 9999.0)
        # The aggregate statement is a GROUP BY over customer_id whose
        # revenue sum is restricted to REVENUE_ORDER_STATUSES (cancelled
        # orders count as orders but not as spend).
        agg_stmt = str(db.captured_statements[-1].compile())
        self.assertIn("GROUP BY", agg_stmt.upper())
        self.assertIn("orders_order.customer_id", agg_stmt.lower())
        compiled = str(db.captured_statements[-1].compile(
            compile_kwargs={"literal_binds": True}
        ))
        for status in ("PENDING_PAYMENT", "DELIVERED", "RETURNED"):
            self.assertIn(status, compiled)
        self.assertNotIn("CANCELLED", compiled)


if __name__ == "__main__":
    unittest.main()
