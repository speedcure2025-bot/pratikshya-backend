"""
Cart schemas — request bodies and response shapes for the cart API.

Response envelope follows the project convention:
  { ok: true, ...payload }  /  { ok: false, error: "..." }
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── Shared config ─────────────────────────────────────────────────────────────

class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ── Coupon DTO (embedded in CartResponse) ─────────────────────────────────────

class CouponSummary(_Base):
    id: str
    code: str
    name: Optional[str] = None
    discount_type: str
    discount_value: float
    minimum_order_value: int = 0


# ── Cart line item ─────────────────────────────────────────────────────────────

class CartItemResponse(_Base):
    """A single resolved line in the cart response."""
    id: str
    product_id: str
    product: Optional[Dict[str, Any]] = None   # StorefrontProduct dict, None if product was deleted
    color: Optional[str] = None
    size: Optional[str] = None
    quantity: int
    added_at: datetime
    line_total: int   # quantity × resolved price (0 if product not found)


# ── Cart totals ───────────────────────────────────────────────────────────────

class CartTotals(_Base):
    subtotal: int
    product_discount: int
    coupon_discount: int
    coupon_code: Optional[str] = None
    offer_id: Optional[str] = None
    shipping: int
    cod_fee: int
    total: int
    saved: int


# ── GET /cart response ────────────────────────────────────────────────────────

class CartResponse(_Base):
    ok: bool = True
    items: List[CartItemResponse] = []
    count: int = 0
    totals: CartTotals
    coupon: Optional[CouponSummary] = None
    coupon_lapsed: bool = False


# ── POST /cart/items ───────────────────────────────────────────────────────────

class AddCartItemRequest(BaseModel):
    product_id: str = Field(..., alias="productId")
    color: Optional[str] = None
    size: Optional[str] = None
    quantity: int = Field(1, ge=1, le=999)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("product_id")
    @classmethod
    def product_id_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("productId is required.")
        return v.strip()


class AddCartItemResponse(_Base):
    ok: bool = True
    message: str = "Item added to cart."
    cart: CartResponse


# ── PATCH /cart/items/{lineId} ────────────────────────────────────────────────

class UpdateCartItemRequest(BaseModel):
    quantity: int = Field(..., ge=0, le=999)


class UpdateCartItemResponse(_Base):
    ok: bool = True
    message: str = "Cart updated."
    cart: CartResponse


# ── DELETE /cart/items/{lineId} ───────────────────────────────────────────────

class RemoveCartItemResponse(_Base):
    ok: bool = True
    message: str = "Item removed from cart."
    cart: CartResponse


# ── DELETE /cart ──────────────────────────────────────────────────────────────

class ClearCartResponse(_Base):
    ok: bool = True
    message: str = "Cart cleared."


# ── POST /cart/coupon ─────────────────────────────────────────────────────────

class ApplyCouponRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=100)

    @field_validator("code")
    @classmethod
    def uppercase_code(cls, v: str) -> str:
        return v.strip().upper()


class ApplyCouponResponse(_Base):
    ok: bool = True
    coupon: CouponSummary
    message: str


# ── DELETE /cart/coupon ───────────────────────────────────────────────────────

class RemoveCouponResponse(_Base):
    ok: bool = True
    message: str = "Coupon removed."


# ── GET /cart/totals ──────────────────────────────────────────────────────────

class CartTotalsResponse(_Base):
    ok: bool = True
    subtotal: int
    product_discount: int
    coupon_discount: int
    coupon_code: Optional[str] = None
    offer_id: Optional[str] = None
    shipping: int
    cod_fee: int
    total: int
    saved: int
