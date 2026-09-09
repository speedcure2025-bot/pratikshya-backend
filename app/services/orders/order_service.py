"""
OrderService — all business logic for the Orders section.

Covers:
  Customer
  ─────────────────────────────────────────────────────────────────────────────
  place_order          POST  /orders
  list_orders          GET   /orders
  get_order            GET   /orders/{orderId}
  get_tracking         GET   /orders/{orderId}/tracking
  cancel_order         POST  /orders/{orderId}/cancel
  create_return        POST  /orders/{orderId}/returns
  get_return           GET   /orders/{orderId}/returns/{returnId}
  claim_guest_orders   POST  /orders/claim-guest

  Admin / Fulfillment
  ─────────────────────────────────────────────────────────────────────────────
  admin_list_orders    GET   /admin/orders
  admin_get_order      GET   /admin/orders/{id}
  allocate             POST  /admin/orders/{id}/allocate
  assign_fulfillment   POST  /admin/orders/{id}/fulfillment
  start_picking        POST  /admin/orders/{id}/pick/start
  pick_item            POST  /admin/orders/{id}/pick/item
  mark_packed          POST  /admin/orders/{id}/pack
  mark_ready           POST  /admin/orders/{id}/ready
  dispatch_order       POST  /admin/orders/{id}/dispatch
  mark_out_for_delivery POST /admin/orders/{id}/out-for-delivery
  mark_delivered       POST  /admin/orders/{id}/deliver
  admin_cancel         POST  /admin/orders/{id}/cancel
  add_note             POST  /admin/orders/{id}/notes
  apply_status         POST  /admin/orders/{id}/status
  force_status         POST  /admin/orders/{id}/force-status
  get_invoice          GET   /admin/orders/{id}/invoice

ORDER_TRANSITIONS (from frontend orderConfig.js — enforced server-side):
  PENDING_PAYMENT      → PAYMENT_CONFIRMED | CANCELLED
  PAYMENT_CONFIRMED    → ORDER_CONFIRMED
  ORDER_CONFIRMED      → ALLOCATED | CANCELLED
  ALLOCATED            → PICKING | CANCELLED
  PICKING              → PACKED
  PACKED               → READY_TO_DISPATCH
  READY_TO_DISPATCH    → SHIPPED
  SHIPPED              → OUT_FOR_DELIVERY
  OUT_FOR_DELIVERY     → DELIVERED
  DELIVERED            → (terminal)
  CANCELLED            → (terminal)

CANCELLABLE_STATUSES (customer):
  PENDING_PAYMENT, PLACED, PAYMENT_CONFIRMED, ORDER_CONFIRMED, CONFIRMED,
  PROCESSING, ALLOCATED, PICKING

ADMIN_CANCELLABLE adds:
  PACKED, READY_TO_DISPATCH
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import (
    BusinessLogicException,
    ConflictException,
    ForbiddenException,
    NotFoundException,
)
from app.models.auth.user import UserModel
from app.models.orders.order import OrderModel
from app.models.orders.order_item import OrderItemModel
from app.models.orders.order_status_history import OrderStatusHistoryModel
from app.schemas.orders.order import (
    AddNoteRequest,
    AdminCancelRequest,
    ApplyStatusRequest,
    CancelOrderRequest,
    CreateReturnRequest,
    DispatchRequest,
    ForceStatusRequest,
    FulfillmentAssignRequest,
    PickItemRequest,
    PlaceOrderRequest,
)

# ── Constants ─────────────────────────────────────────────────────────────────

FREE_SHIPPING_THRESHOLD = 5_000
FLAT_SHIPPING_FEE = 99
EXPRESS_SHIPPING_FEE = 199
COD_FEE = 49

RETURN_WINDOW_DAYS = 7  # default; ideally read from settings

CANCELLABLE_STATUSES = {
    "PENDING_PAYMENT", "PLACED", "PAYMENT_CONFIRMED",
    "ORDER_CONFIRMED", "CONFIRMED", "PROCESSING", "ALLOCATED", "PICKING",
}
ADMIN_CANCELLABLE_STATUSES = CANCELLABLE_STATUSES | {"PACKED", "READY_TO_DISPATCH"}

# Adjacency map — valid forward transitions
ORDER_TRANSITIONS: Dict[str, set] = {
    "PENDING_PAYMENT":    {"PAYMENT_CONFIRMED", "CANCELLED"},
    "PAYMENT_CONFIRMED":  {"ORDER_CONFIRMED"},
    "ORDER_CONFIRMED":    {"ALLOCATED", "CANCELLED"},
    "PLACED":             {"ORDER_CONFIRMED", "ALLOCATED", "CANCELLED"},
    "CONFIRMED":          {"ALLOCATED", "CANCELLED"},
    "PROCESSING":         {"ALLOCATED", "CANCELLED"},
    "ALLOCATED":          {"PICKING", "CANCELLED"},
    "PICKING":            {"PACKED"},
    "PACKED":             {"READY_TO_DISPATCH"},
    "READY_TO_DISPATCH":  {"SHIPPED"},
    "SHIPPED":            {"OUT_FOR_DELIVERY"},
    "OUT_FOR_DELIVERY":   {"DELIVERED"},
    "DELIVERED":          set(),
    "CANCELLED":          set(),
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _generate_order_number() -> str:
    """Generate PF-ORD-XXXXXX (6 random hex digits, upper)."""
    suffix = uuid.uuid4().hex[:6].upper()
    return f"PF-ORD-{suffix}"


def _generate_return_number() -> str:
    suffix = uuid.uuid4().hex[:6].upper()
    return f"PF-RET-{suffix}"


def _can_transition(current: str, next_status: str) -> bool:
    return next_status in ORDER_TRANSITIONS.get(current, set())


def _timeline_event(event: str, actor_id: Optional[str] = None, note: Optional[str] = None) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"event": event, "at": _now_utc().isoformat()}
    if actor_id:
        entry["actorId"] = actor_id
    if note:
        entry["note"] = note
    return entry


def _compute_shipping(subtotal_after_coupon: int, delivery_method: str) -> int:
    if delivery_method == "express":
        return EXPRESS_SHIPPING_FEE  # express is never free
    return 0 if subtotal_after_coupon >= FREE_SHIPPING_THRESHOLD else FLAT_SHIPPING_FEE


def _compute_cod_fee(payment_method: str) -> int:
    return COD_FEE if payment_method == "cod" else 0


def _resolve_unit_price(product: Any) -> tuple[int, int]:
    """
    Resolve the authoritative (unit_price, original_price) for a product
    from the catalog record — the same resolution CartService uses, so
    order totals always match the price the customer was shown.
    """
    base_price = int(product.price or 0)
    original_price = int(getattr(product, "original_price", None) or 0) or base_price
    pricing = getattr(product, "pricing", None) or {}
    if not pricing:
        return base_price, original_price
    selling = int(
        pricing.get("sellingPrice")
        or pricing.get("selling_price")
        or original_price
    )
    disc_type = pricing.get("discountType") or pricing.get("discount_type") or "none"
    disc_val = float(pricing.get("discountValue") or pricing.get("discount_value") or 0)
    if disc_type == "percentage":
        unit_price = max(0, round(selling - selling * disc_val / 100))
    elif disc_type == "fixed":
        unit_price = max(0, round(selling - disc_val))
    else:
        unit_price = selling
    original_price = selling
    return unit_price, original_price


def _order_number_from_key(idempotency_key: str) -> str:
    """
    Derive the unique order_number from a client-supplied idempotency key.

    The `orders_order.order_number` column is UNIQUE in the existing schema,
    which is what makes order-creation idempotent server-side: a retried
    checkout attempt (same key) resolves to the same order_number and is
    returned instead of creating a duplicate order. No new column is needed.
    """
    digest = hashlib.sha1(idempotency_key.encode("utf-8")).hexdigest()[:6].upper()
    return f"PF-ORD-{digest}"


def _same_order_owner(order: OrderModel, customer_id: Optional[str], guest_email: str) -> bool:
    """Ownership check used for idempotent replays of POST /orders."""
    if order.customer_id is not None:
        return order.customer_id == customer_id
    # Guest order: same anonymous caller identified by the same email.
    if customer_id is not None:
        return False
    return (order.guest_email or "").lower() == (guest_email or "").lower()


def _customer_info_dict(order: OrderModel, user: Optional[UserModel]) -> Dict[str, Any]:
    """
    Build the assembled customer identity for an order response.

    - Authenticated order: from the `users` row (authoritative).
    - Guest order: from the guest fields captured at checkout.
    """
    if order.customer_id is not None and user is not None:
        full_name = user.full_name or ""
        parts = full_name.split(None, 1)
        return {
            "firstName": parts[0] if parts else "",
            "lastName": parts[1] if len(parts) > 1 else "",
            "fullName": full_name,
            "email": user.email or "",
            "phone": user.phone,
        }
    address = order.shipping_address or {}
    full_name = address.get("fullName") or ""
    parts = full_name.split(None, 1)
    return {
        "firstName": parts[0] if parts else "",
        "lastName": parts[1] if len(parts) > 1 else "",
        "fullName": full_name,
        "email": order.guest_email or "",
        "phone": order.guest_phone or address.get("phone"),
    }


async def _attach_customer_info(db: AsyncSession, order: OrderModel) -> OrderModel:
    """Attach the transient `customer` projection used by OrderResponse."""
    user: Optional[UserModel] = None
    if order.customer_id:
        stmt = select(UserModel).where(UserModel.id == order.customer_id)
        result = await db.execute(stmt)
        user = result.scalars().first()
    order.customer = _customer_info_dict(order, user)  # type: ignore[attr-defined]
    return order


# ── Order query helper ────────────────────────────────────────────────────────

def _order_load_options() -> tuple:
    """
    Eager-load options shared by every order read.

    Returns are loaded **with their items** because `OrderResponse.returns`
    serialises the real return records (Phase 3 read model) and an async
    session cannot lazy-load them during serialisation.
    """
    from app.models.orders.return_order import ReturnOrderModel

    return (
        selectinload(OrderModel.items),
        selectinload(OrderModel.status_history),
        selectinload(OrderModel.returns).selectinload(ReturnOrderModel.items),
    )


async def _load_order(db: AsyncSession, order_id: str) -> OrderModel:
    stmt = (
        select(OrderModel)
        .where(OrderModel.id == order_id)
        .options(*_order_load_options())
    )
    result = await db.execute(stmt)
    order = result.scalars().first()
    if not order:
        raise NotFoundException(f"Order '{order_id}' not found.")
    return order


async def _load_order_by_number(db: AsyncSession, order_number: str) -> OrderModel:
    stmt = (
        select(OrderModel)
        .where(OrderModel.order_number == order_number)
        .options(*_order_load_options())
    )
    result = await db.execute(stmt)
    order = result.scalars().first()
    if not order:
        raise NotFoundException(f"Order '{order_number}' not found.")
    return order


ORDER_LIST_SORTS = {"newest", "oldest"}


def _list_sort_clause(sort: Optional[str]):
    """Allow-listed customer order-list ordering (defaults to newest first)."""
    if (sort or "").lower() == "oldest":
        return OrderModel.created_at.asc()
    return OrderModel.created_at.desc()


def _append_status_history(
    db: AsyncSession,
    order: OrderModel,
    from_status: str,
    to_status: str,
    actor_id: Optional[str] = None,
    actor_name: Optional[str] = None,
    note: Optional[str] = None,
) -> None:
    entry = OrderStatusHistoryModel(
        id=_new_uuid(),
        order_id=order.id,
        from_status=from_status,
        to_status=to_status,
        actor_id=actor_id,
        actor_name=actor_name,
        note=note,
    )
    db.add(entry)


def _set_status(
    db: AsyncSession,
    order: OrderModel,
    new_status: str,
    actor_id: Optional[str] = None,
    note: Optional[str] = None,
) -> None:
    """Transition order to new_status, writing history and timeline."""
    prev = order.status
    order.status = new_status
    _append_status_history(db, order, prev, new_status, actor_id=actor_id, note=note)
    timeline = list(order.timeline or [])
    timeline.append(_timeline_event(f"STATUS_{new_status}", actor_id=actor_id, note=note))
    order.timeline = timeline


# ── Service ───────────────────────────────────────────────────────────────────

class OrderService:
    """Business logic for the Orders section."""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    # ── Customer: place order ─────────────────────────────────────────────────

    async def place_order(
        self,
        req: PlaceOrderRequest,
        customer_id: Optional[str],
    ) -> OrderModel:
        """
        POST /orders — create and persist a new order (canonical checkout).

        Trust model (Phase 2):
          - Prices are resolved from `catalog_product` — client prices/
            totals/discounts are never trusted (they are not even accepted).
          - Stock is validated and reserved inside this single DB
            transaction: product rows are locked (SELECT ... FOR UPDATE)
            before the stock check so concurrent checkouts cannot oversell.
          - The coupon is revalidated server-side (active, dates, usage
            limits, per-customer limit, eligibility, minimum order value)
            and the discount is recomputed — never taken from the client.
          - Payment method ≠ payment state:
              * COD   → status=ORDER_CONFIRMED, payment_status=PENDING
                        (cash is collected on delivery; no online session)
              * online→ status=PENDING_PAYMENT, payment_status=PENDING
                        (only server-side Razorpay verification/webhook can
                        move this order to PAID / ORDER_CONFIRMED)
          - Idempotency: a client-supplied `idempotencyKey` maps to the
            unique `order_number`; a retried attempt returns the existing
            order instead of creating a duplicate.
        """
        from app.models.catalog.product import ProductModel
        from app.models.commerce.coupon import CouponModel
        from app.models.commerce.coupon_redemption import CouponRedemptionModel

        if not req.items:
            raise BusinessLogicException("Order must contain at least one item.")

        guest_email = (req.customer.email or "").strip().lower()

        # ── Idempotency (server-enforced via unique order_number) ─────────────
        order_number = _generate_order_number()
        if req.idempotency_key:
            order_number = _order_number_from_key(req.idempotency_key)
            existing = await self._find_order_by_number(order_number)
            if existing is not None:
                if _same_order_owner(existing, customer_id, guest_email):
                    # Same checkout attempt retried — return the original order.
                    return await _attach_customer_info(self.db, existing)
                raise ConflictException(
                    "This checkout attempt was already used for a different order. "
                    "Please start a new checkout."
                )

        # ── Load products with row locks (prevents overselling races) ─────────
        product_ids = list({item.product_id for item in req.items})
        stmt = select(ProductModel).where(ProductModel.id.in_(product_ids)).with_for_update()
        result = await self.db.execute(stmt)
        products: Dict[str, Any] = {p.id: p for p in result.scalars().all()}

        # ── Validate lines & compute authoritative prices ─────────────────────
        order_items: List[OrderItemModel] = []
        price_lines: List[Dict[str, Any]] = []
        subtotal = 0
        product_discount = 0
        required_qty: Dict[str, int] = {}

        for line in req.items:
            product = products.get(line.product_id)
            if not product:
                raise BusinessLogicException(f"Product '{line.product_id}' not found.")
            if product.status != "PUBLISHED" or not product.published:
                raise BusinessLogicException(f"Product '{product.name}' is not available.")

            unit_price, original_price = _resolve_unit_price(product)
            line_total = unit_price * line.quantity
            subtotal += line_total
            if original_price > unit_price:
                product_discount += (original_price - unit_price) * line.quantity
            required_qty[line.product_id] = required_qty.get(line.product_id, 0) + line.quantity

            price_lines.append(
                {"product_id": line.product_id, "quantity": line.quantity, "unit_price": unit_price}
            )
            order_items.append(
                OrderItemModel(
                    id=_new_uuid(),
                    product_id=line.product_id,
                    product_name=product.name or "",
                    product_image=getattr(product, "image", None),
                    sku=getattr(product, "sku", None),
                    color=line.color,
                    size=line.size,
                    unit_price=unit_price,
                    original_price=original_price,
                    quantity=line.quantity,
                    line_total=line_total,
                )
            )

        # ── Stock check (rows already locked above) ───────────────────────────
        for pid, qty in required_qty.items():
            product = products[pid]
            available = int(product.stock or 0)
            if product.availability == "out-of-stock" or available <= 0:
                raise BusinessLogicException(f"'{product.name}' is currently out of stock.")
            if qty > available:
                raise BusinessLogicException(
                    f"Insufficient stock for '{product.name}' — only {available} unit(s) available."
                )

        # ── Coupon revalidation (authoritative point) ─────────────────────────
        coupon_discount = 0
        coupon_code: Optional[str] = None
        coupon_id: Optional[str] = None

        if req.coupon_code:
            coupon = await self._find_coupon(req.coupon_code)
            coupon = await self._revalidate_coupon_for_order(
                coupon=coupon,
                subtotal=subtotal,
                price_lines=price_lines,
                customer_id=customer_id,
            )
            coupon_discount = self._compute_coupon_discount(coupon, price_lines)
            coupon_code = coupon.code
            coupon_id = coupon.id
            # Persisted usage increment (existing column) — happens atomically
            # with the order inside this same transaction.
            coupon.usage_count = (coupon.usage_count or 0) + 1

        discounted_subtotal = subtotal - coupon_discount
        shipping_fee = _compute_shipping(discounted_subtotal, req.delivery_method)
        cod_fee = _compute_cod_fee(req.payment_method)
        total = discounted_subtotal + shipping_fee + cod_fee

        # ── Reserve stock (rows are locked; decrement atomically) ─────────────
        for pid, qty in required_qty.items():
            product = products[pid]
            product.stock = int(product.stock or 0) - qty

        # ── Canonical initial status ──────────────────────────────────────────
        is_cod = req.payment_method == "cod"
        status = "ORDER_CONFIRMED" if is_cod else "PENDING_PAYMENT"
        payment_status = "PENDING"  # never PAID at creation — verification only

        if is_cod:
            timeline = [
                _timeline_event("ORDER_CREATED", actor_id=customer_id),
                _timeline_event(
                    "ORDER_CONFIRMED",
                    actor_id=customer_id,
                    note="Cash on delivery — payment due on delivery.",
                ),
            ]
            history_note = "COD order confirmed; payment due on delivery."
        else:
            timeline = [
                _timeline_event("ORDER_CREATED", actor_id=customer_id),
                _timeline_event(
                    "PAYMENT_PENDING",
                    actor_id=customer_id,
                    note="Awaiting online payment verification.",
                ),
            ]
            history_note = "Awaiting online payment verification."

        order = OrderModel(
            id=_new_uuid(),
            order_number=order_number,
            customer_id=customer_id,
            guest_email=None if customer_id else guest_email,
            guest_phone=None if customer_id else req.customer.phone,
            shipping_address={
                "fullName": req.address.full_name,
                "phone": req.address.phone,
                "addressLine": req.address.address_line,
                "landmark": req.address.landmark,
                "city": req.address.city,
                "state": req.address.state,
                "pincode": req.address.pincode,
                "type": req.address.type,
            },
            delivery_method=req.delivery_method,
            payment_method=req.payment_method,
            status=status,
            payment_status=payment_status,
            subtotal=subtotal,
            product_discount=product_discount,
            coupon_discount=coupon_discount,
            shipping_fee=shipping_fee,
            cod_fee=cod_fee,
            total=total,
            coupon_code=coupon_code,
            coupon_id=coupon_id,
            customer_note=req.customer_note,
            inventory_reservation_id=req.inventory_reservation_id,
            timeline=timeline,
            internal_notes=[],
        )

        self.db.add(order)
        await self.db.flush()  # get order.id

        # Attach items
        for item in order_items:
            item.order_id = order.id
            self.db.add(item)

        # Single, accurate status-history seed (no duplicate rows).
        self.db.add(OrderStatusHistoryModel(
            id=_new_uuid(),
            order_id=order.id,
            from_status=None,
            to_status=status,
            actor_id=customer_id,
            note=history_note,
        ))

        # Coupon redemption record — persisted for authenticated customers.
        # (The existing `commerce_coupon_redemption` table requires a user id,
        # so guest redemptions cannot be recorded per-customer; global usage
        # counting still applies. See implementation report.)
        if coupon_id and customer_id:
            self.db.add(CouponRedemptionModel(
                id=_new_uuid(),
                coupon_id=coupon_id,
                customer_id=customer_id,
                order_id=order.id,
                coupon_code=coupon_code,
                discount_amount=coupon_discount,
            ))

        await self.db.flush()
        order = await _load_order(self.db, order.id)
        return await _attach_customer_info(self.db, order)

    async def _find_order_by_number(self, order_number: str) -> Optional[OrderModel]:
        stmt = select(OrderModel).where(OrderModel.order_number == order_number)
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def _find_coupon(self, code: str) -> Optional[Any]:
        from app.models.commerce.coupon import CouponModel
        stmt = select(CouponModel).where(CouponModel.code == (code or "").strip().upper())
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def _revalidate_coupon_for_order(
        self,
        coupon: Optional[Any],
        subtotal: int,
        price_lines: List[Dict[str, Any]],
        customer_id: Optional[str],
    ) -> Any:
        """
        Authoritative coupon revalidation at the order boundary.

        Re-checks everything the client's earlier cart validation may have
        missed or become stale: existence, active flag, date window, global
        usage limit, per-customer usage limit (from persisted redemptions),
        customer eligibility and minimum order value. Raises a user-facing
        BusinessLogicException on any failure.
        """
        from app.models.commerce.coupon_redemption import CouponRedemptionModel

        if coupon is None:
            raise BusinessLogicException("This coupon code is invalid or does not exist.")

        now = _now_utc()
        if not coupon.is_active:
            raise BusinessLogicException("This coupon is no longer active.")
        if coupon.starts_at and coupon.starts_at > now:
            raise BusinessLogicException("This coupon is not yet valid.")
        if coupon.expires_at and coupon.expires_at < now:
            raise BusinessLogicException("This coupon has expired.")
        if coupon.usage_limit is not None and (coupon.usage_count or 0) >= coupon.usage_limit:
            raise BusinessLogicException("This coupon has reached its usage limit.")

        # Per-customer limit (authenticated customers only — the redemption
        # table's customer_id FK requires a user row).
        if coupon.per_customer_limit is not None and customer_id:
            stmt = select(CouponRedemptionModel).where(
                CouponRedemptionModel.coupon_id == coupon.id,
                CouponRedemptionModel.customer_id == customer_id,
            )
            result = await self.db.execute(stmt)
            uses = len(result.scalars().all())
            if uses >= coupon.per_customer_limit:
                raise BusinessLogicException(
                    "You have already used this coupon the maximum number of times."
                )

        # Customer eligibility allow-list (a restricted coupon can never be
        # used by a guest or by a customer outside the list).
        if coupon.eligible_customer_ids:
            if not customer_id or customer_id not in coupon.eligible_customer_ids:
                raise BusinessLogicException("This coupon is not available for your account.")

        if subtotal < (coupon.minimum_order_value or 0):
            raise BusinessLogicException(
                f"This coupon requires a minimum order value of "
                f"₹{(coupon.minimum_order_value or 0):,}. Your subtotal is ₹{subtotal:,}."
            )
        return coupon

    def _compute_coupon_discount(
        self,
        coupon: Any,
        price_lines: List[Dict[str, Any]],
    ) -> int:
        """
        Recompute the coupon discount server-side from authoritative line
        prices. Mirrors CartService semantics, including product
        eligibility/exclusion lists.
        """
        if coupon.discount_type == "free_shipping":
            # Consistent with the cart totals engine: no cash discount.
            return 0

        subtotal = sum(line["unit_price"] * line["quantity"] for line in price_lines)
        eligible_subtotal = subtotal
        if coupon.eligible_product_ids or coupon.excluded_product_ids:
            eligible_subtotal = 0
            for line in price_lines:
                if coupon.excluded_product_ids and line["product_id"] in coupon.excluded_product_ids:
                    continue
                if coupon.eligible_product_ids and line["product_id"] not in coupon.eligible_product_ids:
                    continue
                eligible_subtotal += line["unit_price"] * line["quantity"]

        if coupon.discount_type == "percentage":
            return round(eligible_subtotal * float(coupon.discount_value or 0) / 100)
        if coupon.discount_type == "fixed":
            return min(int(coupon.discount_value or 0), eligible_subtotal)
        return 0

    # ── Customer: list own orders ─────────────────────────────────────────────

    async def list_orders(
        self,
        customer_id: str,
        page: int = 1,
        page_size: int = 20,
        sort: str = "newest",
    ) -> Dict[str, Any]:
        """
        GET /orders — customer's own order list.

        `sort` is an allow-listed ordering (`newest` | `oldest`) applied in
        SQL, so paging stays consistent across pages. Ownership is enforced
        by the `customer_id` filter — the caller's authenticated id, never a
        client-supplied value.
        """
        base_stmt = (
            select(OrderModel)
            .where(OrderModel.customer_id == customer_id)
            .options(*_order_load_options())
            .order_by(_list_sort_clause(sort))
        )

        count_stmt = select(func.count()).select_from(
            select(OrderModel.id).where(OrderModel.customer_id == customer_id).subquery()
        )
        total_result = await self.db.execute(count_stmt)
        total = total_result.scalar_one()

        offset = (page - 1) * page_size
        paginated = base_stmt.offset(offset).limit(page_size)
        result = await self.db.execute(paginated)
        orders = list(result.scalars().all())

        # Batch-attach customer identity (one query, no N+1).
        customer_ids = {o.customer_id for o in orders if o.customer_id}
        users: Dict[str, UserModel] = {}
        if customer_ids:
            user_result = await self.db.execute(
                select(UserModel).where(UserModel.id.in_(customer_ids))
            )
            users = {u.id: u for u in user_result.scalars().all()}
        for o in orders:
            o.customer = _customer_info_dict(o, users.get(o.customer_id))  # type: ignore[attr-defined]

        return {
            "orders": orders,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    # ── Customer: get single order ────────────────────────────────────────────

    async def get_order(self, order_id: str, customer_id: str) -> OrderModel:
        """GET /orders/{orderId} — customer must own the order."""
        order = await _load_order(self.db, order_id)
        if order.customer_id != customer_id:
            raise ForbiddenException("You do not have access to this order.")
        return await _attach_customer_info(self.db, order)

    # ── Customer: tracking ────────────────────────────────────────────────────

    async def get_tracking(self, order_id: str, customer_id: str) -> Dict[str, Any]:
        """
        GET /orders/{orderId}/tracking — real, stored order progress only.

        Phase 3: the previous implementation fabricated shipment events
        (invented locations, `now()` timestamps and courier-style
        descriptions) from the order's current status. That is removed.

        Every returned event is a persisted `orders_order_status_history`
        row with its stored `created_at`. Carrier name, tracking number and
        estimated delivery are whatever an admin recorded on dispatch and
        are `null` otherwise. No courier integration exists, so
        `carrier_events_available` is always `false`.
        """
        order = await _load_order(self.db, order_id)
        if order.customer_id != customer_id:
            raise ForbiddenException("You do not have access to this order.")

        history = sorted(
            order.status_history or [],
            key=lambda entry: entry.created_at,
        )
        events = [
            {
                "status": entry.to_status,
                "from_status": entry.from_status,
                "timestamp": entry.created_at,
                "actor_name": entry.actor_name,
                "note": entry.note,
                "source": "STATUS_HISTORY",
            }
            for entry in history
        ]

        return {
            "order_id": order.id,
            "order_status": order.status,
            "payment_status": order.payment_status,
            "carrier": order.carrier,
            "tracking_number": order.tracking_number,
            "estimated_delivery": order.estimated_delivery,
            "dispatched_at": order.dispatched_at,
            "delivered_at": order.delivered_at,
            "cancelled_at": order.cancelled_at,
            "carrier_tracking_available": bool(order.carrier and order.tracking_number),
            # No courier/carrier API is integrated anywhere in this system.
            "carrier_events_available": False,
            "events": events,
        }

    # ── Customer: cancel ──────────────────────────────────────────────────────

    async def cancel_order(
        self, order_id: str, customer_id: str, req: CancelOrderRequest
    ) -> OrderModel:
        """POST /orders/{orderId}/cancel."""
        order = await _load_order(self.db, order_id)
        if order.customer_id != customer_id:
            raise ForbiddenException("You do not have access to this order.")
        if order.status not in CANCELLABLE_STATUSES:
            raise BusinessLogicException(
                f"Order cannot be cancelled in status '{order.status}'."
            )

        await self._on_order_cancelled(order)
        _set_status(self.db, order, "CANCELLED", actor_id=customer_id, note=req.reason)
        order.cancelled_at = _now_utc()
        order.cancellation_reason = req.reason
        order.cancelled_by = customer_id
        await self.db.flush()
        order = await _load_order(self.db, order.id)
        return await _attach_customer_info(self.db, order)

    async def _on_order_cancelled(self, order: OrderModel) -> None:
        """
        Post-cancellation consistency (single place for both customer and
        admin cancellation paths):
          1. Release the stock reservation for orders that were never paid
             (payment_status PENDING/FAILED). Paid orders keep their stock
             movement in the returns/refund workflow (Phase 3+).
          2. Cancel any active payment session so a cancelled order can no
             longer be charged through a stale Razorpay modal.
        """
        if order.payment_status in ("PENDING", "FAILED"):
            await self._release_stock_reservation(order)
        await self._cancel_active_payment_sessions(order)

    async def _release_stock_reservation(self, order: OrderModel) -> None:
        """Return reserved quantity to `catalog_product.stock` (row-locked)."""
        from app.models.catalog.product import ProductModel

        if not order.items:
            return
        product_ids = [item.product_id for item in order.items]
        result = await self.db.execute(
            select(ProductModel).where(ProductModel.id.in_(product_ids)).with_for_update()
        )
        by_id = {p.id: p for p in result.scalars().all()}
        for item in order.items:
            product = by_id.get(item.product_id)
            if product is not None:
                product.stock = int(product.stock or 0) + item.quantity

    async def _cancel_active_payment_sessions(self, order: OrderModel) -> None:
        from app.models.payments.payment_session import PaymentSessionModel

        result = await self.db.execute(
            select(PaymentSessionModel).where(
                PaymentSessionModel.order_id == order.id,
                PaymentSessionModel.status.in_(["CREATED", "PENDING"]),
            )
        )
        for session in result.scalars().all():
            session.status = "CANCELLED"
            session.cancelled_at = _now_utc()
            session.failure_reason = "Order was cancelled."

    # ── Customer: create return ───────────────────────────────────────────────

    async def create_return(
        self, order_id: str, customer_id: str, req: CreateReturnRequest
    ) -> Any:
        """POST /orders/{orderId}/returns."""
        from app.models.orders.return_order import ReturnOrderModel
        from app.models.orders.return_item import ReturnItemModel

        order = await _load_order(self.db, order_id)
        if order.customer_id != customer_id:
            raise ForbiddenException("You do not have access to this order.")
        if order.status != "DELIVERED":
            raise BusinessLogicException("Returns are only accepted for delivered orders.")

        # Check return window (7 days from delivery)
        if order.delivered_at:
            delta = (_now_utc() - order.delivered_at).days
            if delta > RETURN_WINDOW_DAYS:
                raise BusinessLogicException(
                    f"Return window of {RETURN_WINDOW_DAYS} days has expired."
                )

        # Build item index
        items_by_id = {item.id: item for item in order.items}

        return_items = []
        total_refund = 0

        for ri in req.items:
            order_item = items_by_id.get(ri.line_id)
            if not order_item:
                raise BusinessLogicException(f"Order item '{ri.line_id}' not found in this order.")
            returnable_qty = order_item.quantity - order_item.returned_quantity
            if ri.quantity > returnable_qty:
                raise BusinessLogicException(
                    f"Cannot return {ri.quantity} units of '{order_item.product_name}'; "
                    f"only {returnable_qty} are returnable."
                )
            line_refund = order_item.unit_price * ri.quantity
            total_refund += line_refund

            return_items.append(ReturnItemModel(
                id=_new_uuid(),
                order_item_id=order_item.id,
                product_id=order_item.product_id,
                product_name=order_item.product_name,
                quantity=ri.quantity,
                reason=ri.reason,
                refund_amount=line_refund,
            ))

        return_order = ReturnOrderModel(
            id=_new_uuid(),
            order_id=order.id,
            return_number=_generate_return_number(),
            customer_id=customer_id,
            pickup_method=req.pickup_method,
            status="RETURN_REQUESTED",
            refund_amount=total_refund,
            refund_status="NOT_REQUESTED",
            timeline=[_timeline_event("RETURN_REQUESTED", actor_id=customer_id)],
        )
        self.db.add(return_order)
        await self.db.flush()

        for ri_model in return_items:
            ri_model.return_order_id = return_order.id
            self.db.add(ri_model)

        # Update returned_quantity on order items
        for ri in req.items:
            order_item = items_by_id.get(ri.line_id)
            if order_item:
                order_item.returned_quantity += ri.quantity

        await self.db.flush()
        return await self._load_return(return_order.id)

    # ── Customer: get return ──────────────────────────────────────────────────

    async def get_return(
        self, order_id: str, return_id: str, customer_id: str
    ) -> Any:
        """GET /orders/{orderId}/returns/{returnId}."""
        ret = await self._load_return(return_id)
        if ret.order_id != order_id:
            raise NotFoundException("Return not found for this order.")
        if ret.customer_id != customer_id:
            raise ForbiddenException("You do not have access to this return.")
        return ret

    # ── Customer: claim guest orders ──────────────────────────────────────────

    async def claim_guest_orders(self, account_email: str, customer_id: str) -> int:
        """
        POST /orders/claim-guest — attach guest orders to an account.

        SECURITY (Phase 2): `account_email` MUST be the authenticated
        caller's own verified account email (the router enforces this — a
        client-supplied email that differs from the account email is
        rejected with 403). Guest orders are matched case-insensitively on
        the stored guest email. A caller can therefore only ever take
        ownership of orders placed under their own account's email; guessing
        an order ID or supplying someone else's email is not enough.
        """
        email = (account_email or "").strip().lower()
        if not email:
            raise BusinessLogicException(
                "Your account has no email address, so guest orders cannot be claimed."
            )

        stmt = select(OrderModel).where(
            OrderModel.customer_id.is_(None),
            func.lower(OrderModel.guest_email) == email,
        )
        result = await self.db.execute(stmt)
        guest_orders = result.scalars().all()

        for order in guest_orders:
            order.customer_id = customer_id
            order.guest_email = None  # claimed — no longer a guest order
            timeline = list(order.timeline or [])
            timeline.append(_timeline_event(
                "ORDER_CLAIMED", actor_id=customer_id, note="Guest order attached to account."
            ))
            order.timeline = timeline

        await self.db.flush()
        return len(guest_orders)

    # ── Admin: list orders ────────────────────────────────────────────────────

    async def admin_list_orders(
        self,
        status: Optional[str] = None,
        customer_id: Optional[str] = None,
        q: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """GET /admin/orders."""
        conditions = []
        if status:
            conditions.append(OrderModel.status == status)
        if customer_id:
            conditions.append(OrderModel.customer_id == customer_id)
        if q:
            conditions.append(OrderModel.order_number.ilike(f"%{q}%"))

        base_query = select(OrderModel)
        if conditions:
            base_query = base_query.where(*conditions)

        count_stmt = select(func.count()).select_from(
            base_query.subquery()
        )
        total_result = await self.db.execute(count_stmt)
        total = total_result.scalar_one()

        paginated = (
            base_query
            .options(*_order_load_options())
            .order_by(OrderModel.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self.db.execute(paginated)
        orders = list(result.scalars().all())

        # Attach the same assembled customer projection the customer-facing
        # reads use, so the admin list shows real customer identity instead
        # of a null object (batched — one query, no N+1).
        admin_customer_ids = {o.customer_id for o in orders if o.customer_id}
        admin_users: Dict[str, UserModel] = {}
        if admin_customer_ids:
            user_result = await self.db.execute(
                select(UserModel).where(UserModel.id.in_(admin_customer_ids))
            )
            admin_users = {u.id: u for u in user_result.scalars().all()}
        for o in orders:
            o.customer = _customer_info_dict(o, admin_users.get(o.customer_id))  # type: ignore[attr-defined]

        return {"orders": orders, "total": total, "page": page, "page_size": page_size}

    # ── Admin: get single order ───────────────────────────────────────────────

    async def admin_get_order(self, order_id: str) -> OrderModel:
        """GET /admin/orders/{id} — full record incl. internal notes."""
        order = await _load_order(self.db, order_id)
        return await _attach_customer_info(self.db, order)

    # ── Admin: allocate ───────────────────────────────────────────────────────

    async def allocate(self, order_id: str, actor_id: str) -> OrderModel:
        """POST /admin/orders/{id}/allocate → ALLOCATED."""
        order = await _load_order(self.db, order_id)
        if not _can_transition(order.status, "ALLOCATED"):
            raise BusinessLogicException(
                f"Cannot allocate order in status '{order.status}'."
            )
        _set_status(self.db, order, "ALLOCATED", actor_id=actor_id)
        await self.db.flush()
        return await _load_order(self.db, order.id)

    # ── Admin: assign fulfillment ─────────────────────────────────────────────

    async def assign_fulfillment(
        self, order_id: str, req: FulfillmentAssignRequest, actor_id: str
    ) -> OrderModel:
        """POST /admin/orders/{id}/fulfillment."""
        order = await _load_order(self.db, order_id)
        if req.location_id:
            order.fulfillment_location_id = req.location_id
        if req.handler_id:
            order.fulfillment_handler_id = req.handler_id
        timeline = list(order.timeline or [])
        timeline.append(_timeline_event("FULFILLMENT_ASSIGNED", actor_id=actor_id))
        order.timeline = timeline
        await self.db.flush()
        return await _load_order(self.db, order.id)

    # ── Admin: start picking ──────────────────────────────────────────────────

    async def start_picking(self, order_id: str, actor_id: str) -> OrderModel:
        """POST /admin/orders/{id}/pick/start → PICKING."""
        order = await _load_order(self.db, order_id)
        if not _can_transition(order.status, "PICKING"):
            raise BusinessLogicException(
                f"Cannot start picking in status '{order.status}'."
            )
        _set_status(self.db, order, "PICKING", actor_id=actor_id)
        await self.db.flush()
        return await _load_order(self.db, order.id)

    # ── Admin: pick item ──────────────────────────────────────────────────────

    async def pick_item(
        self, order_id: str, req: PickItemRequest, actor_id: str
    ) -> OrderModel:
        """POST /admin/orders/{id}/pick/item — mark one line as picked."""
        order = await _load_order(self.db, order_id)
        if order.status != "PICKING":
            raise BusinessLogicException("Order must be in PICKING status.")
        # Timeline note only — per-item pick state could extend to an items sub-model
        timeline = list(order.timeline or [])
        timeline.append(_timeline_event(
            "ITEM_PICKED", actor_id=actor_id,
            note=f"item:{req.order_item_id}"
        ))
        order.timeline = timeline
        await self.db.flush()
        return await _load_order(self.db, order.id)

    # ── Admin: pack ───────────────────────────────────────────────────────────

    async def mark_packed(self, order_id: str, actor_id: str) -> OrderModel:
        """POST /admin/orders/{id}/pack → PACKED."""
        order = await _load_order(self.db, order_id)
        if not _can_transition(order.status, "PACKED"):
            raise BusinessLogicException(
                f"Cannot pack order in status '{order.status}'."
            )
        _set_status(self.db, order, "PACKED", actor_id=actor_id)
        await self.db.flush()
        return await _load_order(self.db, order.id)

    # ── Admin: ready to dispatch ──────────────────────────────────────────────

    async def mark_ready(self, order_id: str, actor_id: str) -> OrderModel:
        """POST /admin/orders/{id}/ready → READY_TO_DISPATCH."""
        order = await _load_order(self.db, order_id)
        if not _can_transition(order.status, "READY_TO_DISPATCH"):
            raise BusinessLogicException(
                f"Cannot mark ready in status '{order.status}'."
            )
        _set_status(self.db, order, "READY_TO_DISPATCH", actor_id=actor_id)
        await self.db.flush()
        return await _load_order(self.db, order.id)

    # ── Admin: dispatch ───────────────────────────────────────────────────────

    async def dispatch_order(
        self, order_id: str, req: DispatchRequest, actor_id: str
    ) -> OrderModel:
        """POST /admin/orders/{id}/dispatch → SHIPPED."""
        order = await _load_order(self.db, order_id)
        if not _can_transition(order.status, "SHIPPED"):
            raise BusinessLogicException(
                f"Cannot dispatch order in status '{order.status}'."
            )
        if req.carrier:
            order.carrier = req.carrier
        if req.tracking_number:
            order.tracking_number = req.tracking_number
        if req.estimated_delivery:
            order.estimated_delivery = req.estimated_delivery

        order.dispatched_at = _now_utc()
        _set_status(self.db, order, "SHIPPED", actor_id=actor_id)
        await self.db.flush()
        return await _load_order(self.db, order.id)

    # ── Admin: out for delivery ───────────────────────────────────────────────

    async def mark_out_for_delivery(self, order_id: str, actor_id: str) -> OrderModel:
        """POST /admin/orders/{id}/out-for-delivery → OUT_FOR_DELIVERY."""
        order = await _load_order(self.db, order_id)
        if not _can_transition(order.status, "OUT_FOR_DELIVERY"):
            raise BusinessLogicException(
                f"Cannot mark out-for-delivery in status '{order.status}'."
            )
        _set_status(self.db, order, "OUT_FOR_DELIVERY", actor_id=actor_id)
        await self.db.flush()
        return await _load_order(self.db, order.id)

    # ── Admin: deliver ────────────────────────────────────────────────────────

    async def mark_delivered(self, order_id: str, actor_id: str) -> OrderModel:
        """POST /admin/orders/{id}/deliver → DELIVERED."""
        order = await _load_order(self.db, order_id)
        if not _can_transition(order.status, "DELIVERED"):
            raise BusinessLogicException(
                f"Cannot mark delivered in status '{order.status}'."
            )
        order.delivered_at = _now_utc()
        _set_status(self.db, order, "DELIVERED", actor_id=actor_id)
        await self.db.flush()
        return await _load_order(self.db, order.id)

    # ── Admin: cancel ─────────────────────────────────────────────────────────

    async def admin_cancel(
        self, order_id: str, req: AdminCancelRequest, actor_id: str
    ) -> OrderModel:
        """POST /admin/orders/{id}/cancel — broader cancellable set."""
        order = await _load_order(self.db, order_id)
        if order.status not in ADMIN_CANCELLABLE_STATUSES:
            raise BusinessLogicException(
                f"Order cannot be cancelled in status '{order.status}'."
            )
        await self._on_order_cancelled(order)
        _set_status(self.db, order, "CANCELLED", actor_id=actor_id, note=req.reason)
        order.cancelled_at = _now_utc()
        order.cancellation_reason = req.reason
        order.cancelled_by = actor_id
        await self.db.flush()
        return await _load_order(self.db, order.id)

    # ── Admin: add note ───────────────────────────────────────────────────────

    async def add_note(
        self, order_id: str, req: AddNoteRequest, actor_id: str, actor_name: Optional[str] = None
    ) -> OrderModel:
        """POST /admin/orders/{id}/notes."""
        order = await _load_order(self.db, order_id)
        notes = list(order.internal_notes or [])
        notes.append({
            "id": _new_uuid(),
            "authorId": actor_id,
            "authorName": actor_name or actor_id,
            "note": req.note,
            "createdAt": _now_utc().isoformat(),
        })
        order.internal_notes = notes
        timeline = list(order.timeline or [])
        timeline.append(_timeline_event("NOTE_ADDED", actor_id=actor_id))
        order.timeline = timeline
        await self.db.flush()
        return await _load_order(self.db, order.id)

    # ── Admin: apply validated status ─────────────────────────────────────────

    async def apply_status(
        self, order_id: str, req: ApplyStatusRequest, actor_id: str
    ) -> OrderModel:
        """POST /admin/orders/{id}/status — validated by ORDER_TRANSITIONS."""
        order = await _load_order(self.db, order_id)
        if not _can_transition(order.status, req.status):
            raise BusinessLogicException(
                f"Transition '{order.status}' → '{req.status}' is not allowed."
            )
        _set_status(self.db, order, req.status, actor_id=actor_id, note=req.note)
        await self.db.flush()
        return await _load_order(self.db, order.id)

    # ── Admin: force status (bypasses adjacency) ──────────────────────────────

    async def force_status(
        self, order_id: str, req: ForceStatusRequest, actor_id: str
    ) -> OrderModel:
        """POST /admin/orders/{id}/force-status — bypasses ORDER_TRANSITIONS; always audited."""
        order = await _load_order(self.db, order_id)
        # Force — no adjacency check; reason is mandatory (enforced by schema)
        _set_status(
            self.db, order, req.status,
            actor_id=actor_id,
            note=f"[FORCE] {req.reason}",
        )
        await self.db.flush()
        return await _load_order(self.db, order.id)

    # ── Admin: invoice ────────────────────────────────────────────────────────

    async def get_invoice(self, order_id: str) -> Dict[str, Any]:
        """
        GET /admin/orders/{id}/invoice — invoice metadata only.

        Reports honestly whether an invoice number has actually been issued.
        No invoice document is generated anywhere in this system, so
        `document_available` is always False and no URL is returned.
        """
        order = await _load_order(self.db, order_id)
        return {
            "order_id": order.id,
            "invoice_number": order.invoice_number,
            "issued_at": order.invoice_issued_at,
            "available": bool(order.invoice_number),
            "document_available": False,
        }

    # ── Internal: load return ─────────────────────────────────────────────────

    async def _load_return(self, return_id: str) -> Any:
        from app.models.orders.return_order import ReturnOrderModel
        from app.models.orders.return_item import ReturnItemModel

        stmt = (
            select(ReturnOrderModel)
            .where(ReturnOrderModel.id == return_id)
            .options(selectinload(ReturnOrderModel.items))
        )
        result = await self.db.execute(stmt)
        ret = result.scalars().first()
        if not ret:
            raise NotFoundException(f"Return '{return_id}' not found.")
        return ret
