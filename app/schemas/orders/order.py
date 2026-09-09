"""
Pydantic schemas for the Orders section.

Covers all 24 ORDERS endpoints from API_CONTRACT.md:
  Customer:
    POST  /orders                        PlaceOrderRequest  → OrderResponse
    GET   /orders                        OrderListResponse
    GET   /orders/{orderId}              OrderResponse
    GET   /orders/{orderId}/tracking     TrackingResponse
    POST  /orders/{orderId}/cancel       CancelOrderRequest → OrderResponse
    POST  /orders/{orderId}/returns      CreateReturnRequest → ReturnResponse
    GET   /orders/{orderId}/returns/{id} ReturnResponse
    POST  /orders/claim-guest            ClaimGuestOrdersRequest → OkResponse

  Admin (fulfillment + management):
    GET   /admin/orders                  AdminOrderListResponse
    GET   /admin/orders/{id}             AdminOrderDetailResponse
    POST  /admin/orders/{id}/allocate
    POST  /admin/orders/{id}/fulfillment FulfillmentAssignRequest
    POST  /admin/orders/{id}/pick/start
    POST  /admin/orders/{id}/pick/item   PickItemRequest
    POST  /admin/orders/{id}/pack
    POST  /admin/orders/{id}/ready
    POST  /admin/orders/{id}/dispatch    DispatchRequest
    POST  /admin/orders/{id}/out-for-delivery
    POST  /admin/orders/{id}/deliver
    POST  /admin/orders/{id}/cancel      AdminCancelRequest
    POST  /admin/orders/{id}/notes       AddNoteRequest
    POST  /admin/orders/{id}/status      ApplyStatusRequest
    POST  /admin/orders/{id}/force-status ForceStatusRequest
    GET   /admin/orders/{id}/invoice     InvoiceResponse

  Admin Returns desk:
    GET  /admin/returns
    GET  /admin/returns/{id}
    POST /admin/returns/{id}/approve
    POST /admin/returns/{id}/reject       ReturnRejectRequest
    POST /admin/returns/{id}/schedule-pickup SchedulePickupRequest
    POST /admin/returns/{id}/receive      ReceiveReturnRequest
    POST /admin/returns/{id}/inspect      InspectReturnRequest
    POST /admin/returns/{id}/refund/initiate
    POST /admin/returns/{id}/refund/complete
"""

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Basic RFC-5322-ish email shape check (no external dependency required).
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


# ── Shared address shape (matches customer address contract) ──────────────────

class AddressSnapshot(BaseModel):
    full_name: str = Field(..., alias="fullName")
    phone: str
    address_line: str = Field(..., alias="addressLine")
    landmark: Optional[str] = None
    city: str
    state: str
    pincode: str
    type: Optional[str] = "Home"

    model_config = ConfigDict(populate_by_name=True)


# ── Order item shapes ─────────────────────────────────────────────────────────

class PlaceOrderItem(BaseModel):
    product_id: str = Field(..., alias="productId")
    color: Optional[str] = None
    size: Optional[str] = None
    quantity: int = Field(..., ge=1, le=99)

    model_config = ConfigDict(populate_by_name=True)


class OrderItemResponse(BaseModel):
    id: str
    product_id: str
    product_name: str
    product_image: Optional[str] = None
    sku: Optional[str] = None
    color: Optional[str] = None
    size: Optional[str] = None
    unit_price: int
    original_price: int
    quantity: int
    line_total: int
    returned_quantity: int = 0

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


# ── Status history ────────────────────────────────────────────────────────────

class StatusHistoryEntry(BaseModel):
    id: str
    from_status: Optional[str] = None
    to_status: str
    actor_id: Optional[str] = None
    actor_name: Optional[str] = None
    note: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ── Tracking shapes ───────────────────────────────────────────────────────────

class TrackingEvent(BaseModel):
    """
    A real, recorded order event.

    Every event returned by the tracking endpoint is a persisted
    `orders_order_status_history` row — its `timestamp` is the stored
    `created_at`, never a projected or estimated value. No carrier event
    feed exists in this system, so no carrier events are ever synthesised.
    """
    status: str
    timestamp: datetime
    from_status: Optional[str] = None
    actor_name: Optional[str] = None
    note: Optional[str] = None
    source: str = "STATUS_HISTORY"


class TrackingResponse(BaseModel):
    """
    Order tracking, backed exclusively by stored order data.

    `carrier` / `tracking_number` / `estimated_delivery` are the values an
    admin recorded on dispatch (`POST /admin/orders/{id}/dispatch`); they
    are `null` until that happens. `carrier_tracking_available` states
    honestly whether a shipment identity exists, and
    `carrier_events_available` is always `false` — no courier integration
    exists in this system.
    """
    ok: bool = True
    order_id: str
    order_status: str
    payment_status: str
    carrier: Optional[str] = None
    tracking_number: Optional[str] = None
    estimated_delivery: Optional[datetime] = None
    dispatched_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    carrier_tracking_available: bool = False
    carrier_events_available: bool = False
    events: List[TrackingEvent] = []

    model_config = ConfigDict(populate_by_name=True)


# ── Return response shapes (declared before OrderResponse so the customer
# order read model can embed the order's real return records) ────────────────

class ReturnItemResponse(BaseModel):
    id: str
    order_item_id: str
    product_id: str
    product_name: str
    quantity: int
    reason: Optional[str] = None
    refund_amount: int = 0

    model_config = ConfigDict(from_attributes=True)


class ReturnResponse(BaseModel):
    id: str
    order_id: str
    return_number: str
    customer_id: Optional[str] = None
    status: str
    pickup_method: str
    refund_amount: int = 0
    refund_status: str
    items: List[ReturnItemResponse] = []
    timeline: Optional[List[Dict[str, Any]]] = []
    rejection_reason: Optional[str] = None
    rejection_reason_customer: Optional[str] = None
    package_condition: Optional[str] = None
    inspection_condition: Optional[str] = None
    inspection_notes: Optional[str] = None
    refund_method: Optional[str] = None
    refund_initiated_at: Optional[datetime] = None
    refund_completed_at: Optional[datetime] = None
    pickup_scheduled_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)



# ── Full order response ───────────────────────────────────────────────────────

class OrderResponse(BaseModel):
    id: str
    order_number: str
    customer_id: Optional[str] = None
    guest_email: Optional[str] = None
    # Assembled customer identity (Phase 2: required by the order
    # confirmation flow). Built server-side from the authenticated user
    # (or guest fields) — never trusted from the client.
    customer: Optional[Dict[str, Any]] = None
    status: str
    payment_status: str
    payment_method: str
    delivery_method: str
    shipping_address: Optional[Dict[str, Any]] = None
    items: List[OrderItemResponse] = []
    subtotal: int
    product_discount: int
    coupon_discount: int
    coupon_code: Optional[str] = None
    shipping_fee: int
    cod_fee: int
    total: int
    customer_note: Optional[str] = None
    timeline: Optional[List[Dict[str, Any]]] = []
    status_history: List[StatusHistoryEntry] = []
    tracking_number: Optional[str] = None
    carrier: Optional[str] = None
    estimated_delivery: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    dispatched_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    cancellation_reason: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_issued_at: Optional[datetime] = None
    # Real return records raised against this order (Phase 3: the order
    # read model must expose the actual return state instead of the UI
    # inferring one). Empty list = no return has ever been requested.
    returns: List[ReturnResponse] = []
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SingleOrderResponse(BaseModel):
    ok: bool = True
    order: OrderResponse


class OrderListResponse(BaseModel):
    ok: bool = True
    orders: List[OrderResponse]
    total: int
    page: int = 1
    page_size: int = 20


# ── Admin order response (includes internal_notes) ────────────────────────────

class AdminOrderResponse(OrderResponse):
    internal_notes: Optional[List[Dict[str, Any]]] = []
    fulfillment_location_id: Optional[str] = None
    fulfillment_handler_id: Optional[str] = None


class AdminSingleOrderResponse(BaseModel):
    ok: bool = True
    order: AdminOrderResponse


class AdminOrderListResponse(BaseModel):
    ok: bool = True
    orders: List[AdminOrderResponse]
    total: int
    page: int = 1
    page_size: int = 20


# ── Place order ───────────────────────────────────────────────────────────────

class CustomerSnapshot(BaseModel):
    """
    Customer snapshot for order placement.

    Canonical contract (single mapping, no implicit guessing):
      - The client MUST send `firstName` and `lastName` separately.
        A single `fullName` string is NOT accepted — the backend does not
        split/guess arbitrary name strings.
      - `email` is required and shape-validated. For guest checkout it is
        the identity used later for the (server-verified) order claim.
    """
    first_name: str = Field(..., alias="firstName", min_length=1, max_length=100)
    last_name: str = Field(..., alias="lastName", min_length=1, max_length=100)
    email: str = Field(..., min_length=3, max_length=255)
    phone: Optional[str] = Field(None, max_length=30)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("first_name", "last_name")
    @classmethod
    def _names_must_not_be_blank(cls, v: str) -> str:
        value = (v or "").strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("email")
    @classmethod
    def _email_shape(cls, v: str) -> str:
        value = (v or "").strip().lower()
        if not _EMAIL_RE.match(value):
            raise ValueError("a valid email address is required")
        return value

    @field_validator("phone")
    @classmethod
    def _phone_trim(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        value = v.strip()
        return value or None


class PlaceOrderRequest(BaseModel):
    """
    POST /orders — canonical checkout request.

    Trust rules enforced at this boundary:
      - No client-supplied totals/prices/discounts: the backend resolves
        prices from `catalog_product`, revalidates the coupon and
        recalculates subtotal/discount/shipping/COD fee/total.
      - `paymentMethod` only states HOW the customer intends to pay.
        It NEVER implies payment success — online orders start in
        `PENDING_PAYMENT` / `payment_status = PENDING` and are only
        confirmed by server-side payment verification.
      - `idempotencyKey` (optional, client-generated per checkout attempt)
        is enforced server-side through the unique `orders_order.order_number`
        constraint: retries with the same key return the same order instead
        of creating a duplicate.
    """
    items: List[PlaceOrderItem]
    customer: CustomerSnapshot
    address: AddressSnapshot
    delivery_method: str = Field("standard", alias="deliveryMethod")
    payment_method: str = Field(..., alias="paymentMethod")
    coupon_code: Optional[str] = Field(None, alias="couponCode", max_length=50)
    customer_note: Optional[str] = Field(None, alias="customerNote")
    inventory_reservation_id: Optional[str] = Field(None, alias="inventoryReservationId")
    idempotency_key: Optional[str] = Field(
        None, alias="idempotencyKey", min_length=8, max_length=100,
        description="Client-generated checkout attempt key (safe retries)",
    )

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("delivery_method")
    @classmethod
    def _delivery_method_allowed(cls, v: str) -> str:
        allowed = {"standard", "express"}
        if v not in allowed:
            raise ValueError(f"delivery_method must be one of {sorted(allowed)}")
        return v

    @field_validator("payment_method")
    @classmethod
    def _payment_method_allowed(cls, v: str) -> str:
        allowed = {"upi", "card", "netbanking", "cod"}
        if v not in allowed:
            raise ValueError(f"payment_method must be one of {sorted(allowed)}")
        return v

    @field_validator("coupon_code")
    @classmethod
    def _coupon_code_trim(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        value = v.strip().upper()
        return value or None


# ── Cancel order ──────────────────────────────────────────────────────────────

class CancelOrderRequest(BaseModel):
    reason: Optional[str] = None


# ── Return order ──────────────────────────────────────────────────────────────

class ReturnItemRequest(BaseModel):
    line_id: str = Field(..., alias="lineId")
    quantity: int = Field(..., ge=1)
    reason: str

    model_config = ConfigDict(populate_by_name=True)


class CreateReturnRequest(BaseModel):
    items: List[ReturnItemRequest]
    pickup_method: str = Field("SCHEDULED_PICKUP", alias="pickupMethod")

    model_config = ConfigDict(populate_by_name=True)


class SingleReturnResponse(BaseModel):
    ok: bool = True
    return_order: ReturnResponse


class ReturnListResponse(BaseModel):
    ok: bool = True
    returns: List[ReturnResponse]
    total: int


# ── Claim guest orders ────────────────────────────────────────────────────────

class ClaimGuestOrdersRequest(BaseModel):
    """
    POST /orders/claim-guest

    `email` is OPTIONAL and, when supplied, MUST equal the authenticated
    user's own account email. The server always derives the claim identity
    from the authenticated account — it never trusts an arbitrary email
    to reassign another person's guest orders.
    """
    email: Optional[str] = None


class OkResponse(BaseModel):
    ok: bool = True
    message: Optional[str] = None


class ClaimGuestOrdersResponse(BaseModel):
    ok: bool = True
    message: Optional[str] = None
    claimed: int = 0


# ── Admin fulfillment requests ────────────────────────────────────────────────

class FulfillmentAssignRequest(BaseModel):
    location_id: Optional[str] = Field(None, alias="locationId")
    handler_id: Optional[str] = Field(None, alias="handlerId")

    model_config = ConfigDict(populate_by_name=True)


class PickItemRequest(BaseModel):
    order_item_id: str = Field(..., alias="orderItemId")

    model_config = ConfigDict(populate_by_name=True)


class DispatchRequest(BaseModel):
    carrier: Optional[str] = None
    tracking_number: Optional[str] = Field(None, alias="trackingNumber")
    estimated_delivery: Optional[datetime] = Field(None, alias="estimatedDelivery")

    model_config = ConfigDict(populate_by_name=True)


class AdminCancelRequest(BaseModel):
    reason: Optional[str] = None


class AddNoteRequest(BaseModel):
    note: str


class ApplyStatusRequest(BaseModel):
    status: str
    note: Optional[str] = None


class ForceStatusRequest(BaseModel):
    status: str
    reason: str   # Required for force transitions — always audited


class InvoiceResponse(BaseModel):
    """
    Invoice metadata for an order.

    The existing schema stores only `invoice_number` / `invoice_issued_at`.
    There is **no invoice document generator, no PDF pipeline and no
    invoice file storage** in this system, so no download URL is ever
    returned. `available` is `true` only when a real invoice number has
    actually been issued for the order; `document_available` is always
    `false` until an invoicing service exists.
    """
    ok: bool = True
    order_id: str
    invoice_number: Optional[str] = None
    issued_at: Optional[datetime] = None
    available: bool = False
    document_available: bool = False

    model_config = ConfigDict(populate_by_name=True)


# ── Admin returns desk ────────────────────────────────────────────────────────

class ReturnRejectRequest(BaseModel):
    reason: str   # Internal reason code
    customer_message: Optional[str] = Field(None, alias="customerMessage")

    model_config = ConfigDict(populate_by_name=True)


class SchedulePickupRequest(BaseModel):
    scheduled_at: datetime = Field(..., alias="scheduledAt")
    pickup_address: Optional[Dict[str, Any]] = Field(None, alias="pickupAddress")

    model_config = ConfigDict(populate_by_name=True)


class ReceiveReturnRequest(BaseModel):
    package_condition: str = Field(..., alias="packageCondition")
    notes: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class InspectReturnRequest(BaseModel):
    inspection_condition: str = Field(..., alias="inspectionCondition")
    notes: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)
