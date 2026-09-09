"""
Cart — API router.

URL mapping (API_CONTRACT.md → implementation):

  GET    /cart                        ← restore + return full cart
  POST   /cart/items                  ← add item (merge duplicate lines)
  PATCH  /cart/items/{lineId}         ← update quantity (< 1 removes)
  DELETE /cart/items/{lineId}         ← remove single line
  DELETE /cart                        ← clear entire cart
  POST   /cart/coupon                 ← apply & validate coupon
  DELETE /cart/coupon                 ← detach coupon
  GET    /cart/totals                 ← totals breakdown only

Authorization: Customer JWT required on all endpoints.
              Guest carts remain client-only (per spec).
"""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_customer, get_db
from app.models.auth.user import UserModel
from app.schemas.commerce.cart import (
    AddCartItemRequest,
    AddCartItemResponse,
    ApplyCouponRequest,
    ApplyCouponResponse,
    CartResponse,
    CartTotalsResponse,
    ClearCartResponse,
    CouponSummary,
    RemoveCartItemResponse,
    RemoveCouponResponse,
    UpdateCartItemRequest,
    UpdateCartItemResponse,
)
from app.services.commerce.cart_service import CartService

router = APIRouter(prefix="/cart", tags=["Cart"])


# ===========================================================================
# GET /cart — full cart with restored lines, resolved products, and totals
# ===========================================================================

@router.get(
    "",
    response_model=CartResponse,
    summary="Get current customer cart",
    description=(
        "Returns `{ items, count, totals, coupon, coupon_lapsed }`.  \n\n"
        "**Restore rules applied on every read:**  \n"
        "- Drop lines whose product is deleted or unpublished.  \n"
        "- Clamp quantity to available stock.  \n"
        "- Merge duplicate `(productId, color, size)` lines.  \n"
        "- Invalidate lapsed coupons (`coupon_lapsed = true`)."
    ),
)
async def get_cart(
    current_user: UserModel = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    service = CartService(db)
    return await service.get_cart(current_user.id)


# ===========================================================================
# POST /cart/items — add item or increment existing line
# ===========================================================================

@router.post(
    "/items",
    response_model=AddCartItemResponse,
    status_code=status.HTTP_200_OK,
    summary="Add item to cart",
    description=(
        "Body: `{ productId, color?, size?, quantity }`.  \n\n"
        "**Line identity:** `(productId, color, size)`. "
        "Same triple → same line; quantities add.  \n\n"
        "Validates product is `PUBLISHED` and has stock. "
        "Clamps to available stock if requested quantity exceeds it."
    ),
)
async def add_cart_item(
    req: AddCartItemRequest,
    current_user: UserModel = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    service = CartService(db)
    cart = await service.add_item(current_user.id, req)
    return AddCartItemResponse(cart=cart)


# ===========================================================================
# PATCH /cart/items/{lineId} — update quantity
# ===========================================================================

@router.patch(
    "/items/{line_id}",
    response_model=UpdateCartItemResponse,
    summary="Update cart item quantity",
    description=(
        "Body: `{ quantity }`.  \n"
        "`quantity < 1` removes the line entirely.  \n"
        "`lineId` is the SHA-1 hash of `productId::color::size` "
        "(returned as `id` in each cart item)."
    ),
)
async def update_cart_item(
    line_id: str,
    req: UpdateCartItemRequest,
    current_user: UserModel = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    service = CartService(db)
    cart = await service.update_item(current_user.id, line_id, req.quantity)
    return UpdateCartItemResponse(cart=cart)


# ===========================================================================
# DELETE /cart/items/{lineId} — remove single line
# ===========================================================================

@router.delete(
    "/items/{line_id}",
    response_model=RemoveCartItemResponse,
    summary="Remove item from cart",
    description="Removes the cart line identified by `lineId`.",
)
async def remove_cart_item(
    line_id: str,
    current_user: UserModel = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    service = CartService(db)
    cart = await service.remove_item(current_user.id, line_id)
    return RemoveCartItemResponse(cart=cart)


# ===========================================================================
# DELETE /cart — clear entire cart
# ===========================================================================

@router.delete(
    "",
    response_model=ClearCartResponse,
    summary="Clear entire cart",
    description="Removes all lines and detaches any applied coupon.",
)
async def clear_cart(
    current_user: UserModel = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    service = CartService(db)
    await service.clear_cart(current_user.id)
    return ClearCartResponse()


# ===========================================================================
# POST /cart/coupon — apply coupon
# ===========================================================================

@router.post(
    "/coupon",
    response_model=ApplyCouponResponse,
    summary="Apply coupon code to cart",
    description=(
        "Body: `{ code }`.  \n\n"
        "**Validation checks:**  \n"
        "- Coupon exists and is active.  \n"
        "- Within valid date window.  \n"
        "- Global usage limit not reached.  \n"
        "- Per-customer limit not reached.  \n"
        "- Customer is in the eligibility list (if set).  \n"
        "- Cart subtotal ≥ minimum order value.  \n\n"
        "Response: `{ ok: true, coupon, message: '<CODE> is now part of your order.' }`"
    ),
)
async def apply_coupon(
    req: ApplyCouponRequest,
    current_user: UserModel = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    service = CartService(db)
    coupon = await service.apply_coupon(current_user.id, req.code)
    return ApplyCouponResponse(
        coupon=CouponSummary(
            id=coupon.id,
            code=coupon.code,
            name=coupon.name,
            discount_type=coupon.discount_type,
            discount_value=coupon.discount_value,
            minimum_order_value=coupon.minimum_order_value,
        ),
        message=f"{coupon.code} is now part of your order.",
    )


# ===========================================================================
# DELETE /cart/coupon — remove coupon
# ===========================================================================

@router.delete(
    "/coupon",
    response_model=RemoveCouponResponse,
    summary="Remove applied coupon from cart",
)
async def remove_coupon(
    current_user: UserModel = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    service = CartService(db)
    await service.remove_coupon(current_user.id)
    return RemoveCouponResponse()


# ===========================================================================
# GET /cart/totals — totals breakdown only
# ===========================================================================

@router.get(
    "/totals",
    response_model=CartTotalsResponse,
    summary="Get cart totals breakdown",
    description=(
        "Returns `{ subtotal, productDiscount, couponDiscount, couponCode, offerId, "
        "shipping, codFee, total, saved }`.  \n\n"
        "Query params:  \n"
        "- `deliveryMethod`: `standard` (default) | `express`  \n"
        "- `paymentMethod`: `online` (default) | `cod`  \n\n"
        "**Pricing constants (authoritative, shared with the order boundary):**  \n"
        "- Free shipping threshold: ₹5,000 (standard only — express is never free)  \n"
        "- Standard shipping fee: ₹99  \n"
        "- Express shipping fee: ₹199  \n"
        "- COD surcharge: ₹49"
    ),
)
async def get_cart_totals(
    delivery_method: str = Query("standard", alias="deliveryMethod"),
    payment_method: str = Query("online", alias="paymentMethod"),
    current_user: UserModel = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    service = CartService(db)
    return await service.get_totals(
        current_user.id,
        delivery_method=delivery_method,
        payment_method=payment_method,
    )
