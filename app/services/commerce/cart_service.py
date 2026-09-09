"""
CartService — all business logic for the server-side shopping cart.

Covers:
  - get_cart          GET /cart
  - add_item          POST /cart/items
  - update_item       PATCH /cart/items/{lineId}
  - remove_item       DELETE /cart/items/{lineId}
  - clear_cart        DELETE /cart
  - apply_coupon      POST /cart/coupon
  - remove_coupon     DELETE /cart/coupon
  - get_totals        GET /cart/totals

Restore rules (applied on every cart read):
  - Drop lines whose product no longer resolves or is unpublished.
  - Clamp quantity to available stock (stock > 0 and availability != "out-of-stock").
  - Merge duplicate (productId, color, size) triples (sum quantities).
  - Keep the coupon only if it still resolves to a live, valid offer;
    set coupon_lapsed = True otherwise.

Pricing constants (backend is authority; cart and order boundary share them):
  FREE_SHIPPING_THRESHOLD = 5000
  FLAT_SHIPPING_FEE       = 99
  EXPRESS_SHIPPING_FEE    = 199   (express is never free — order-boundary rule)
  COD_FEE                 = 49
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import TTL_CART, cache
from app.core.exceptions import BusinessLogicException, NotFoundException
from app.core.logging import get_logger
from app.models.catalog.product import ProductModel
from app.models.commerce.cart import CartModel
from app.models.commerce.cart_item import CartItemModel
from app.models.commerce.coupon import CouponModel
from app.models.commerce.coupon_redemption import CouponRedemptionModel
from app.schemas.commerce.cart import (
    AddCartItemRequest,
    CartItemResponse,
    CartResponse,
    CartTotals,
    CartTotalsResponse,
    CouponSummary,
)

logger = get_logger(__name__)

# ── Pricing constants ─────────────────────────────────────────────────────────
FREE_SHIPPING_THRESHOLD = 5_000   # ₹5,000 — free standard shipping above this
FLAT_SHIPPING_FEE = 99            # ₹99 flat standard shipping fee
EXPRESS_SHIPPING_FEE = 199        # ₹199 express fee — never free (order boundary rule)
COD_FEE = 49                      # ₹49 cash-on-delivery surcharge


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _cart_line_id(product_id: str, color: Optional[str], size: Optional[str]) -> str:
    """
    Deterministic line identifier: SHA-1 hex of "productId::color::size".
    Matches the frontend cartLineId() function semantics.
    """
    key = f"{product_id}::{(color or '').lower()}::{(size or '').lower()}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def _effective_price(product: ProductModel) -> int:
    """Resolve the customer-facing final price from the product model."""
    pricing = product.pricing or {}
    if pricing:
        discount_type = pricing.get("discountType") or pricing.get("discount_type") or "none"
        selling = int(pricing.get("sellingPrice") or pricing.get("selling_price") or product.price or 0)
        discount_value = float(pricing.get("discountValue") or pricing.get("discount_value") or 0)
        if discount_type == "percentage":
            return max(0, round(selling - selling * discount_value / 100))
        elif discount_type == "fixed":
            return max(0, round(selling - discount_value))
        return selling
    return int(product.price or 0)


# ── Service ───────────────────────────────────────────────────────────────────

class CartService:
    """Business logic for the server-side shopping cart."""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    # ── Internal: get or create cart ──────────────────────────────────────────

    async def _get_or_create_cart(self, customer_id: str) -> CartModel:
        """Return the customer's cart, creating one if it doesn't exist yet."""
        stmt = select(CartModel).where(CartModel.customer_id == customer_id)
        result = await self.db.execute(stmt)
        cart = result.scalars().first()
        if not cart:
            cart = CartModel(customer_id=customer_id)
            self.db.add(cart)
            await self.db.flush()
        return cart

    # ── Internal: resolve and restore cart ────────────────────────────────────

    async def _resolve_products(self, product_ids: List[str]) -> Dict[str, ProductModel]:
        """Bulk-fetch products by their IDs; return a dict keyed by product id."""
        if not product_ids:
            return {}
        stmt = select(ProductModel).where(ProductModel.id.in_(product_ids))
        result = await self.db.execute(stmt)
        return {p.id: p for p in result.scalars().all()}

    async def _restore_cart(self, cart: CartModel) -> Tuple[List[CartItemModel], bool]:
        """
        Apply restore rules:
          1. Drop lines whose product is gone or not PUBLISHED.
          2. Clamp quantity to available stock.
          3. Merge duplicate (product_id, color, size) lines.
        Returns (cleaned_items, coupon_still_valid).
        """
        if not cart.items:
            return [], await self._validate_coupon_still_live(cart)

        product_ids = list({item.product_id for item in cart.items})
        products = await self._resolve_products(product_ids)

        # Step 1 & 2 — drop invalid, clamp quantity
        valid_items: List[CartItemModel] = []
        to_delete: List[CartItemModel] = []

        for item in cart.items:
            product = products.get(item.product_id)
            if not product or product.status != "PUBLISHED" or not product.published:
                to_delete.append(item)
                continue
            max_qty = max(0, int(product.stock or 0))
            if product.availability in ("out-of-stock",) or max_qty == 0:
                to_delete.append(item)
                continue
            if item.quantity > max_qty:
                item.quantity = max_qty
                if item.quantity < 1:
                    to_delete.append(item)
                    continue
            valid_items.append(item)

        for dead in to_delete:
            await self.db.delete(dead)
        if to_delete:
            await self.db.flush()

        # Step 3 — merge duplicates
        seen: Dict[str, CartItemModel] = {}
        dupes: List[CartItemModel] = []
        for item in valid_items:
            key = _cart_line_id(item.product_id, item.color, item.size)
            if key in seen:
                seen[key].quantity += item.quantity
                dupes.append(item)
            else:
                seen[key] = item

        for dupe in dupes:
            await self.db.delete(dupe)
        if dupes:
            await self.db.flush()

        return list(seen.values()), await self._validate_coupon_still_live(cart)

    async def _validate_coupon_still_live(self, cart: CartModel) -> bool:
        """
        Return True if the applied coupon is still valid (resolves + active + not expired).
        Side-effect: sets cart.coupon_lapsed = True if it has lapsed.
        """
        if not cart.coupon_code:
            cart.coupon_lapsed = False
            return True

        coupon = await self._find_coupon(cart.coupon_code)
        if not coupon:
            cart.coupon_lapsed = True
            return False

        now = _now_utc()
        if not coupon.is_active:
            cart.coupon_lapsed = True
            return False
        if coupon.expires_at and coupon.expires_at < now:
            cart.coupon_lapsed = True
            return False
        if coupon.usage_limit is not None and coupon.usage_count >= coupon.usage_limit:
            cart.coupon_lapsed = True
            return False

        cart.coupon_lapsed = False
        return True

    async def _find_coupon(self, code: str) -> Optional[CouponModel]:
        stmt = select(CouponModel).where(CouponModel.code == code.upper())
        result = await self.db.execute(stmt)
        return result.scalars().first()

    # ── Internal: build response DTOs ─────────────────────────────────────────

    def _product_to_dict(self, product: ProductModel) -> Dict[str, Any]:
        """Lightweight storefront projection used inside cart items."""
        return {
            "id": product.id,
            "name": product.name or "",
            "slug": product.slug or "",
            "image": product.image or "",
            "price": _effective_price(product),
            "stock": int(product.stock or 0),
            "availability": product.availability or "in-stock",
            "color": product.primary_color or "",
            "colors": product.colors or [],
            "sizes": product.sizes or [],
        }

    async def _build_cart_response(
        self,
        cart: CartModel,
        items: List[CartItemModel],
        coupon_valid: bool,
        delivery_method: str = "standard",
        payment_method: str = "online",
    ) -> CartResponse:
        """Build the full CartResponse DTO from a restored cart."""
        product_ids = [item.product_id for item in items]
        products = await self._resolve_products(product_ids)

        item_responses: List[CartItemResponse] = []
        subtotal = 0
        product_discount = 0

        for item in items:
            product = products.get(item.product_id)
            if not product:
                continue
            price = _effective_price(product)
            original = int(product.original_price or 0) or int(product.price or 0)
            line_total = price * item.quantity
            line_original_total = original * item.quantity
            subtotal += line_total
            if line_original_total > line_total:
                product_discount += (line_original_total - line_total)

            item_responses.append(
                CartItemResponse(
                    id=_cart_line_id(item.product_id, item.color, item.size),
                    product_id=item.product_id,
                    product=self._product_to_dict(product),
                    color=item.color,
                    size=item.size,
                    quantity=item.quantity,
                    added_at=item.added_at,
                    line_total=line_total,
                )
            )

        # Coupon discount
        coupon_discount = 0
        coupon_summary: Optional[CouponSummary] = None
        offer_id: Optional[str] = None

        if cart.coupon_code and coupon_valid:
            coupon = await self._find_coupon(cart.coupon_code)
            if coupon:
                coupon_discount = self._compute_coupon_discount(coupon, subtotal, items, products)
                coupon_summary = CouponSummary(
                    id=coupon.id,
                    code=coupon.code,
                    name=coupon.name,
                    discount_type=coupon.discount_type,
                    discount_value=coupon.discount_value,
                    minimum_order_value=coupon.minimum_order_value,
                )
                offer_id = coupon.id

        discounted_subtotal = subtotal - coupon_discount
        # Shipping must mirror the order-boundary rule in
        # services/orders/order_service._compute_shipping so the cart display
        # and the placed order can never disagree: express carries a flat
        # premium at every order value (never free); standard is complimentary
        # at/above the free-shipping threshold and ₹99 below it.
        if delivery_method == "express":
            shipping = EXPRESS_SHIPPING_FEE
        elif delivery_method == "free":
            shipping = 0
        else:
            shipping = (
                0 if discounted_subtotal >= FREE_SHIPPING_THRESHOLD else FLAT_SHIPPING_FEE
            )
        cod_fee = COD_FEE if payment_method == "cod" else 0
        total = max(0, discounted_subtotal + shipping + cod_fee)
        saved = product_discount + coupon_discount + (
            FLAT_SHIPPING_FEE if shipping == 0 and subtotal > 0 else 0
        )

        totals = CartTotals(
            subtotal=subtotal,
            product_discount=product_discount,
            coupon_discount=coupon_discount,
            coupon_code=cart.coupon_code if coupon_valid else None,
            offer_id=offer_id,
            shipping=shipping,
            cod_fee=cod_fee,
            total=total,
            saved=saved,
        )

        return CartResponse(
            ok=True,
            items=item_responses,
            count=sum(i.quantity for i in item_responses),
            totals=totals,
            coupon=coupon_summary,
            coupon_lapsed=cart.coupon_lapsed,
        )

    def _compute_coupon_discount(
        self,
        coupon: CouponModel,
        subtotal: int,
        items: List[CartItemModel],
        products: Dict[str, ProductModel],
    ) -> int:
        """Compute the rupee coupon discount for the current cart subtotal."""
        if subtotal < coupon.minimum_order_value:
            return 0

        eligible_subtotal = subtotal
        if coupon.eligible_product_ids or coupon.excluded_product_ids:
            eligible_subtotal = 0
            for item in items:
                p = products.get(item.product_id)
                if not p:
                    continue
                if coupon.excluded_product_ids and item.product_id in coupon.excluded_product_ids:
                    continue
                if coupon.eligible_product_ids and item.product_id not in coupon.eligible_product_ids:
                    continue
                eligible_subtotal += _effective_price(p) * item.quantity

        if coupon.discount_type == "percentage":
            return round(eligible_subtotal * coupon.discount_value / 100)
        elif coupon.discount_type == "fixed":
            return min(int(coupon.discount_value), eligible_subtotal)
        elif coupon.discount_type == "free_shipping":
            return 0   # handled in shipping calculation
        return 0

    # ── Cache helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _cart_cache_key(customer_id: str) -> str:
        return f"cart:{customer_id}"

    async def _invalidate_cart_cache(self, customer_id: str) -> None:
        """Evict the cart cache after any mutation."""
        await cache.delete(self._cart_cache_key(customer_id))

    # ── Public API ─────────────────────────────────────────────────────────────

    async def get_cart(self, customer_id: str) -> CartResponse:
        """
        GET /cart — restore and return the full cart.

        Cache strategy: read-through with TTL_CART seconds.
        Any mutation (add/update/remove/coupon/clear) invalidates the cache.
        """
        cache_key = self._cart_cache_key(customer_id)
        cached = await cache.get_json(cache_key)
        if cached:
            try:
                return CartResponse(**cached)
            except Exception:
                logger.warning("Stale cart cache schema for customer_id — falling through to DB", exc_info=True)
                pass  # Stale schema — fall through to DB

        cart = await self._get_or_create_cart(customer_id)
        items, coupon_valid = await self._restore_cart(cart)
        response = await self._build_cart_response(cart, items, coupon_valid)

        await cache.set_json(cache_key, response.model_dump(), TTL_CART)
        return response

    async def add_item(self, customer_id: str, req: AddCartItemRequest) -> CartResponse:
        """
        POST /cart/items — add a product line or increment an existing one.
        Line identity: (productId, color, size).
        """
        cart = await self._get_or_create_cart(customer_id)

        # Validate product exists and is purchasable
        stmt = select(ProductModel).where(ProductModel.id == req.product_id)
        result = await self.db.execute(stmt)
        product = result.scalars().first()
        if not product or product.status != "PUBLISHED" or not product.published:
            raise NotFoundException(f"Product '{req.product_id}' is not available.")

        max_qty = int(product.stock or 0)
        if product.availability in ("out-of-stock",) or max_qty == 0:
            raise BusinessLogicException(f"'{product.name}' is currently out of stock.")

        # Find existing line or create new one
        existing_stmt = select(CartItemModel).where(
            CartItemModel.cart_id == cart.id,
            CartItemModel.product_id == req.product_id,
            CartItemModel.color == req.color,
            CartItemModel.size == req.size,
        )
        existing_result = await self.db.execute(existing_stmt)
        existing = existing_result.scalars().first()

        if existing:
            new_qty = existing.quantity + req.quantity
            existing.quantity = min(new_qty, max_qty)
        else:
            new_item = CartItemModel(
                cart_id=cart.id,
                product_id=req.product_id,
                color=req.color,
                size=req.size,
                quantity=min(req.quantity, max_qty),
                added_at=_now_utc(),
            )
            self.db.add(new_item)

        await self.db.flush()
        items, coupon_valid = await self._restore_cart(cart)
        response = await self._build_cart_response(cart, items, coupon_valid)
        await self._invalidate_cart_cache(customer_id)
        return response

    async def update_item(
        self, customer_id: str, line_id: str, quantity: int
    ) -> CartResponse:
        """
        PATCH /cart/items/{lineId} — update quantity.
        quantity < 1 removes the line.
        """
        cart = await self._get_or_create_cart(customer_id)

        # Match line by the computed lineId hash
        stmt = select(CartItemModel).where(CartItemModel.cart_id == cart.id)
        result = await self.db.execute(stmt)
        all_items = result.scalars().all()

        target: Optional[CartItemModel] = None
        for item in all_items:
            if _cart_line_id(item.product_id, item.color, item.size) == line_id:
                target = item
                break

        if not target:
            raise NotFoundException("Cart line not found.")

        if quantity < 1:
            await self.db.delete(target)
        else:
            # Clamp to available stock
            stmt2 = select(ProductModel).where(ProductModel.id == target.product_id)
            res2 = await self.db.execute(stmt2)
            product = res2.scalars().first()
            max_qty = int(product.stock or 0) if product else 0
            target.quantity = min(quantity, max_qty) if max_qty > 0 else quantity

        await self.db.flush()
        items, coupon_valid = await self._restore_cart(cart)
        response = await self._build_cart_response(cart, items, coupon_valid)
        await self._invalidate_cart_cache(customer_id)
        return response

    async def remove_item(self, customer_id: str, line_id: str) -> CartResponse:
        """DELETE /cart/items/{lineId} — remove a single line."""
        cart = await self._get_or_create_cart(customer_id)

        stmt = select(CartItemModel).where(CartItemModel.cart_id == cart.id)
        result = await self.db.execute(stmt)
        all_items = result.scalars().all()

        target: Optional[CartItemModel] = None
        for item in all_items:
            if _cart_line_id(item.product_id, item.color, item.size) == line_id:
                target = item
                break

        if not target:
            raise NotFoundException("Cart line not found.")

        await self.db.delete(target)
        await self.db.flush()
        items, coupon_valid = await self._restore_cart(cart)
        response = await self._build_cart_response(cart, items, coupon_valid)
        await self._invalidate_cart_cache(customer_id)
        return response

    async def clear_cart(self, customer_id: str) -> None:
        """DELETE /cart — remove all lines and the coupon."""
        cart = await self._get_or_create_cart(customer_id)
        for item in list(cart.items):
            await self.db.delete(item)
        cart.coupon_code = None
        cart.coupon_id = None
        cart.coupon_lapsed = False
        await self.db.flush()
        await self._invalidate_cart_cache(customer_id)

    async def apply_coupon(
        self, customer_id: str, code: str
    ) -> CouponModel:
        """
        POST /cart/coupon — validate and apply a coupon code.
        Raises BusinessLogicException with a user-facing message on any failure.
        """
        cart = await self._get_or_create_cart(customer_id)
        coupon = await self._find_coupon(code)

        if not coupon:
            raise BusinessLogicException("This coupon code is invalid or does not exist.")

        now = _now_utc()

        if not coupon.is_active:
            raise BusinessLogicException("This coupon is no longer active.")

        if coupon.starts_at and coupon.starts_at > now:
            raise BusinessLogicException("This coupon is not yet valid.")

        if coupon.expires_at and coupon.expires_at < now:
            raise BusinessLogicException("This coupon has expired.")

        if coupon.usage_limit is not None and coupon.usage_count >= coupon.usage_limit:
            raise BusinessLogicException("This coupon has reached its usage limit.")

        # Per-customer limit check
        if coupon.per_customer_limit is not None:
            redemption_stmt = select(CouponRedemptionModel).where(
                CouponRedemptionModel.coupon_id == coupon.id,
                CouponRedemptionModel.customer_id == customer_id,
            )
            redemption_result = await self.db.execute(redemption_stmt)
            customer_uses = len(redemption_result.scalars().all())
            if customer_uses >= coupon.per_customer_limit:
                raise BusinessLogicException(
                    "You have already used this coupon the maximum number of times."
                )

        # Customer eligibility
        if coupon.eligible_customer_ids and customer_id not in coupon.eligible_customer_ids:
            raise BusinessLogicException("This coupon is not available for your account.")

        # Minimum order value check (against current subtotal)
        items, _ = await self._restore_cart(cart)
        product_ids = [item.product_id for item in items]
        products = await self._resolve_products(product_ids)
        subtotal = sum(
            _effective_price(products[item.product_id]) * item.quantity
            for item in items
            if item.product_id in products
        )

        if subtotal < coupon.minimum_order_value:
            raise BusinessLogicException(
                f"This coupon requires a minimum order value of ₹{coupon.minimum_order_value:,}. "
                f"Your current subtotal is ₹{subtotal:,}."
            )

        # Apply
        cart.coupon_code = coupon.code
        cart.coupon_id = coupon.id
        cart.coupon_lapsed = False
        await self.db.flush()
        await self._invalidate_cart_cache(customer_id)
        return coupon

    async def remove_coupon(self, customer_id: str) -> None:
        """DELETE /cart/coupon — detach the coupon from the cart."""
        cart = await self._get_or_create_cart(customer_id)
        cart.coupon_code = None
        cart.coupon_id = None
        cart.coupon_lapsed = False
        await self.db.flush()
        await self._invalidate_cart_cache(customer_id)

    async def get_totals(
        self,
        customer_id: str,
        delivery_method: str = "standard",
        payment_method: str = "online",
    ) -> CartTotalsResponse:
        """GET /cart/totals — compute and return only the totals breakdown."""
        cart = await self._get_or_create_cart(customer_id)
        items, coupon_valid = await self._restore_cart(cart)
        cart_resp = await self._build_cart_response(
            cart, items, coupon_valid, delivery_method, payment_method
        )
        t = cart_resp.totals
        return CartTotalsResponse(
            ok=True,
            subtotal=t.subtotal,
            product_discount=t.product_discount,
            coupon_discount=t.coupon_discount,
            coupon_code=t.coupon_code,
            offer_id=t.offer_id,
            shipping=t.shipping,
            cod_fee=t.cod_fee,
            total=t.total,
            saved=t.saved,
        )
