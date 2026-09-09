"""
Orders — API router.

URL mapping (API_CONTRACT.md → implementation):

  Customer
  ─────────────────────────────────────────────────────────────────────────────
  POST /orders                              ← place order (auth OR guest)
  GET  /orders                              ← customer's own orders
  GET  /orders/{orderId}                    ← single order detail
  GET  /orders/{orderId}/tracking           ← carrier / tracking events
  POST /orders/{orderId}/cancel             ← cancel (CANCELLABLE_STATUSES)
  POST /orders/{orderId}/returns            ← create return (DELIVERED only)
  GET  /orders/{orderId}/returns/{returnId} ← return detail
  POST /orders/claim-guest                  ← attach guest orders by email

  Admin — Fulfillment pipeline
  ─────────────────────────────────────────────────────────────────────────────
  GET  /admin/orders                        ← full list (orders.view)
  GET  /admin/orders/{id}                   ← full record incl. internal_notes
  POST /admin/orders/{id}/allocate          ← → ALLOCATED
  POST /admin/orders/{id}/fulfillment       ← assign location/handler
  POST /admin/orders/{id}/pick/start        ← → PICKING
  POST /admin/orders/{id}/pick/item         ← per-line pick record
  POST /admin/orders/{id}/pack              ← → PACKED
  POST /admin/orders/{id}/ready             ← → READY_TO_DISPATCH
  POST /admin/orders/{id}/dispatch          ← → SHIPPED (+ carrier/tracking)
  POST /admin/orders/{id}/out-for-delivery  ← → OUT_FOR_DELIVERY
  POST /admin/orders/{id}/deliver           ← → DELIVERED
  POST /admin/orders/{id}/cancel            ← broader cancel set
  POST /admin/orders/{id}/notes             ← add internal note
  POST /admin/orders/{id}/status            ← validated status transition
  POST /admin/orders/{id}/force-status      ← bypass adjacency (audited)
  GET  /admin/orders/{id}/invoice           ← invoice stub

  Admin — Returns desk
  ─────────────────────────────────────────────────────────────────────────────
  GET  /admin/returns                       ← all returns (returns.view)
  GET  /admin/returns/{id}                  ← return detail
  POST /admin/returns/{id}/approve          ← → APPROVED
  POST /admin/returns/{id}/reject           ← → REJECTED
  POST /admin/returns/{id}/schedule-pickup  ← → PICKUP_SCHEDULED
  POST /admin/returns/{id}/receive          ← → RECEIVED
  POST /admin/returns/{id}/inspect          ← → INSPECTED
  POST /admin/returns/{id}/refund/initiate  ← → REFUND_INITIATED
  POST /admin/returns/{id}/refund/complete  ← → REFUNDED
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessLogicException, ForbiddenException
from app.dependencies import (
    get_current_admin,
    get_current_customer,
    get_db,
    get_optional_user,
)
from app.models.auth.user import UserModel
from app.schemas.orders.order import (
    AddNoteRequest,
    AdminCancelRequest,
    AdminOrderListResponse,
    AdminSingleOrderResponse,
    ApplyStatusRequest,
    CancelOrderRequest,
    ClaimGuestOrdersRequest,
    ClaimGuestOrdersResponse,
    CreateReturnRequest,
    DispatchRequest,
    ForceStatusRequest,
    FulfillmentAssignRequest,
    InspectReturnRequest,
    InvoiceResponse,
    OrderListResponse,
    PickItemRequest,
    PlaceOrderRequest,
    ReceiveReturnRequest,
    ReturnListResponse,
    ReturnRejectRequest,
    ReturnResponse,
    SchedulePickupRequest,
    SingleOrderResponse,
    SingleReturnResponse,
    TrackingResponse,
)
from app.services.orders.order_service import OrderService
from app.services.orders.return_service import ReturnService

router = APIRouter(tags=["Orders"])


# ===========================================================================
# CUSTOMER — place an order (authenticated OR guest)
# ===========================================================================

@router.post(
    "/orders",
    response_model=SingleOrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Place a new order (canonical checkout)",
    description=(
        "Authorization: **Customer session or guest** (guest orders are claimable later "
        "via the verified-email claim flow).  \n\n"
        "**Initial state (payment method ≠ payment state):**  \n"
        "- COD → `status = ORDER_CONFIRMED`, `paymentStatus = PENDING` "
        "(cash collected on delivery; no online payment session)  \n"
        "- UPI / card / netbanking → `status = PENDING_PAYMENT`, `paymentStatus = PENDING` "
        "(only server-side Razorpay verification/webhook can mark it PAID)  \n"
        "- `statusHistory` seeded: `PENDING_PAYMENT → PAYMENT_CONFIRMED → ORDER_CONFIRMED`  \n"
        "- `timeline` seeded: `ORDER_CREATED`, `PAYMENT_CONFIRMED`, `ORDER_CONFIRMED`  \n\n"
        "**Pricing (authoritative, server-computed):**  \n"
        "- Prices resolved from the catalogue — client totals/prices/discounts are not accepted  \n"
        "- Free shipping threshold: ₹5,000 · Standard: ₹99 · Express: ₹199 (never free) · COD surcharge: ₹49  \n"
        "- Coupon revalidated server-side (active / dates / usage limits / eligibility / minimum order)  \n\n"
        "**Stock:** validated and reserved under row locks in this transaction.  \n\n"
        "**Idempotency:** optional `idempotencyKey` — a retried attempt returns the same order."
    ),
)
async def place_order(
    req: PlaceOrderRequest,
    # Optional auth — guest allowed (no token = guest order)
    current_user: Optional[UserModel] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    service = OrderService(db)
    customer_id = current_user.id if current_user else None
    order = await service.place_order(req, customer_id=customer_id)
    return SingleOrderResponse(order=order)


# ===========================================================================
# CUSTOMER — list own orders
# ===========================================================================

@router.get(
    "/orders",
    response_model=OrderListResponse,
    summary="List customer's own orders",
    description=(
        "Authorization: **Customer session**. Returns only the authenticated "
        "customer's own orders — ownership is enforced by the query itself.  \n\n"
        "Paginated (`page`, `pageSize`) with `total` for the full result set. "
        "`sort` is allow-listed: `newest` (default) or `oldest`."
    ),
)
async def list_orders(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100, alias="pageSize"),
    sort: str = Query("newest", pattern="^(newest|oldest)$"),
    current_user: UserModel = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    service = OrderService(db)
    result = await service.list_orders(
        current_user.id, page=page, page_size=page_size, sort=sort
    )
    return OrderListResponse(
        orders=result["orders"],
        total=result["total"],
        page=result["page"],
        page_size=result["page_size"],
    )


# ===========================================================================
# CUSTOMER — claim guest orders (must appear before /{orderId} to avoid conflict)
# ===========================================================================

@router.post(
    "/orders/claim-guest",
    response_model=ClaimGuestOrdersResponse,
    summary="Claim guest orders after sign-in",
    description=(
        "Attaches guest orders (placed without an account) to the currently "
        "authenticated customer account.  \\n\\n"
        "**Security:** the claim identity is always derived from the "
        "authenticated account's own email. If a different email is "
        "supplied, the request is rejected (403) — a caller can never "
        "reassign another person's guest orders by supplying their email.  \\n\\n"
        "Idempotent: orders already claimed are not claimed again."
    ),
)
async def claim_guest_orders(
    req: ClaimGuestOrdersRequest,
    current_user: UserModel = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    account_email = (current_user.email or "").strip().lower()
    if not account_email:
        raise BusinessLogicException(
            "Your account has no email address, so guest orders cannot be claimed."
        )

    # Never trust a caller-supplied email: it must equal the account's own email.
    supplied = (req.email or "").strip().lower()
    if supplied and supplied != account_email:
        raise ForbiddenException(
            "You can only claim guest orders for your own account email."
        )

    service = OrderService(db)
    claimed = await service.claim_guest_orders(account_email, current_user.id)
    return ClaimGuestOrdersResponse(
        ok=True,
        message=f"{claimed} order(s) claimed successfully.",
        claimed=claimed,
    )


# ===========================================================================
# CUSTOMER — single order
# ===========================================================================

@router.get(
    "/orders/{order_id}",
    response_model=SingleOrderResponse,
    summary="Get order detail",
)
async def get_order(
    order_id: str,
    current_user: UserModel = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    service = OrderService(db)
    order = await service.get_order(order_id, current_user.id)
    return SingleOrderResponse(order=order)


# ===========================================================================
# CUSTOMER — tracking
# ===========================================================================

@router.get(
    "/orders/{order_id}/tracking",
    response_model=TrackingResponse,
    summary="Get order tracking information",
    description=(
        "Authorization: **Customer session** (must own the order).  \n\n"
        "Returns **only real, stored order progress**: every event is a "
        "persisted status-history row with its recorded timestamp. Carrier "
        "name / tracking number / estimated delivery are the values recorded "
        "by an admin at dispatch and are `null` until then.  \n\n"
        "No courier integration exists, so `carrierEventsAvailable` is always "
        "`false` and no shipment scan events are ever synthesised."
    ),
)
async def get_tracking(
    order_id: str,
    current_user: UserModel = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    service = OrderService(db)
    data = await service.get_tracking(order_id, current_user.id)
    return TrackingResponse(**data)


# ===========================================================================
# CUSTOMER — cancel order
# ===========================================================================

@router.post(
    "/orders/{order_id}/cancel",
    response_model=SingleOrderResponse,
    summary="Cancel an order",
    description=(
        "Allowed statuses: `PENDING_PAYMENT`, `PLACED`, `PAYMENT_CONFIRMED`, "
        "`ORDER_CONFIRMED`, `CONFIRMED`, `PROCESSING`, `ALLOCATED`, `PICKING`."
    ),
)
async def cancel_order(
    order_id: str,
    req: CancelOrderRequest,
    current_user: UserModel = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    service = OrderService(db)
    order = await service.cancel_order(order_id, current_user.id, req)
    return SingleOrderResponse(order=order)


# ===========================================================================
# CUSTOMER — create return
# ===========================================================================

@router.post(
    "/orders/{order_id}/returns",
    response_model=SingleReturnResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a return request",
    description=(
        "Precondition: `status === DELIVERED` and within the return window "
        "(default 7 days).  \n"
        "Body: `{ items: [{ lineId, quantity, reason }], pickupMethod }`."
    ),
)
async def create_return(
    order_id: str,
    req: CreateReturnRequest,
    current_user: UserModel = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    service = OrderService(db)
    return_order = await service.create_return(order_id, current_user.id, req)
    return SingleReturnResponse(return_order=return_order)


# ===========================================================================
# CUSTOMER — get return
# ===========================================================================

@router.get(
    "/orders/{order_id}/returns/{return_id}",
    response_model=SingleReturnResponse,
    summary="Get return detail",
)
async def get_return(
    order_id: str,
    return_id: str,
    current_user: UserModel = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    service = OrderService(db)
    return_order = await service.get_return(order_id, return_id, current_user.id)
    return SingleReturnResponse(return_order=return_order)


# ===========================================================================
# ADMIN — list orders
# ===========================================================================

@router.get(
    "/admin/orders",
    response_model=AdminOrderListResponse,
    summary="Admin — list all orders",
    description=(
        "Authorization: `orders.view`.  \n"
        "Filters: `status`, `customerId`, `q` (order number search).  \n"
        "Returns full records including `internal_notes`, `fulfillment_location_id`, "
        "`fulfillment_handler_id`."
    ),
)
async def admin_list_orders(
    status_filter: Optional[str] = Query(None, alias="status"),
    customer_id: Optional[str] = Query(None, alias="customerId"),
    q: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100, alias="pageSize"),
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = OrderService(db)
    result = await service.admin_list_orders(
        status=status_filter,
        customer_id=customer_id,
        q=q,
        page=page,
        page_size=page_size,
    )
    return AdminOrderListResponse(
        orders=result["orders"],
        total=result["total"],
        page=result["page"],
        page_size=result["page_size"],
    )


# ===========================================================================
# ADMIN — get order detail
# ===========================================================================

@router.get(
    "/admin/orders/{order_id}",
    response_model=AdminSingleOrderResponse,
    summary="Admin — get full order record",
    description="Authorization: `orders.view`. Includes internal notes.",
)
async def admin_get_order(
    order_id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = OrderService(db)
    order = await service.admin_get_order(order_id)
    return AdminSingleOrderResponse(order=order)


# ===========================================================================
# ADMIN — allocate
# ===========================================================================

@router.post(
    "/admin/orders/{order_id}/allocate",
    response_model=AdminSingleOrderResponse,
    summary="Admin — allocate order → ALLOCATED",
    description="Authorization: `orders.fulfill`.",
)
async def admin_allocate(
    order_id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = OrderService(db)
    order = await service.allocate(order_id, actor_id=current_user.id)
    return AdminSingleOrderResponse(order=order)


# ===========================================================================
# ADMIN — assign fulfillment
# ===========================================================================

@router.post(
    "/admin/orders/{order_id}/fulfillment",
    response_model=AdminSingleOrderResponse,
    summary="Admin — assign fulfillment location and handler",
    description="Authorization: `orders.fulfill`.",
)
async def admin_assign_fulfillment(
    order_id: str,
    req: FulfillmentAssignRequest,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = OrderService(db)
    order = await service.assign_fulfillment(order_id, req, actor_id=current_user.id)
    return AdminSingleOrderResponse(order=order)


# ===========================================================================
# ADMIN — start picking
# ===========================================================================

@router.post(
    "/admin/orders/{order_id}/pick/start",
    response_model=AdminSingleOrderResponse,
    summary="Admin — start picking → PICKING",
    description="Authorization: `orders.pick`.",
)
async def admin_start_picking(
    order_id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = OrderService(db)
    order = await service.start_picking(order_id, actor_id=current_user.id)
    return AdminSingleOrderResponse(order=order)


# ===========================================================================
# ADMIN — pick item
# ===========================================================================

@router.post(
    "/admin/orders/{order_id}/pick/item",
    response_model=AdminSingleOrderResponse,
    summary="Admin — mark a line item as picked",
    description="Authorization: `orders.pick`. Body: `{ orderItemId }`.",
)
async def admin_pick_item(
    order_id: str,
    req: PickItemRequest,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = OrderService(db)
    order = await service.pick_item(order_id, req, actor_id=current_user.id)
    return AdminSingleOrderResponse(order=order)


# ===========================================================================
# ADMIN — pack
# ===========================================================================

@router.post(
    "/admin/orders/{order_id}/pack",
    response_model=AdminSingleOrderResponse,
    summary="Admin — mark order as packed → PACKED",
    description="Authorization: `orders.pack`.",
)
async def admin_pack(
    order_id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = OrderService(db)
    order = await service.mark_packed(order_id, actor_id=current_user.id)
    return AdminSingleOrderResponse(order=order)


# ===========================================================================
# ADMIN — ready to dispatch
# ===========================================================================

@router.post(
    "/admin/orders/{order_id}/ready",
    response_model=AdminSingleOrderResponse,
    summary="Admin — mark order ready to dispatch → READY_TO_DISPATCH",
    description="Authorization: `orders.pack`.",
)
async def admin_ready(
    order_id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = OrderService(db)
    order = await service.mark_ready(order_id, actor_id=current_user.id)
    return AdminSingleOrderResponse(order=order)


# ===========================================================================
# ADMIN — dispatch
# ===========================================================================

@router.post(
    "/admin/orders/{order_id}/dispatch",
    response_model=AdminSingleOrderResponse,
    summary="Admin — dispatch order → SHIPPED",
    description=(
        "Authorization: `orders.dispatch`.  \n"
        "Body: `{ carrier?, trackingNumber?, estimatedDelivery? }`."
    ),
)
async def admin_dispatch(
    order_id: str,
    req: DispatchRequest,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = OrderService(db)
    order = await service.dispatch_order(order_id, req, actor_id=current_user.id)
    return AdminSingleOrderResponse(order=order)


# ===========================================================================
# ADMIN — out for delivery
# ===========================================================================

@router.post(
    "/admin/orders/{order_id}/out-for-delivery",
    response_model=AdminSingleOrderResponse,
    summary="Admin — mark order out for delivery → OUT_FOR_DELIVERY",
    description="Authorization: `orders.dispatch`.",
)
async def admin_out_for_delivery(
    order_id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = OrderService(db)
    order = await service.mark_out_for_delivery(order_id, actor_id=current_user.id)
    return AdminSingleOrderResponse(order=order)


# ===========================================================================
# ADMIN — deliver
# ===========================================================================

@router.post(
    "/admin/orders/{order_id}/deliver",
    response_model=AdminSingleOrderResponse,
    summary="Admin — mark order delivered → DELIVERED",
    description="Authorization: `orders.dispatch`.",
)
async def admin_deliver(
    order_id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = OrderService(db)
    order = await service.mark_delivered(order_id, actor_id=current_user.id)
    return AdminSingleOrderResponse(order=order)


# ===========================================================================
# ADMIN — cancel
# ===========================================================================

@router.post(
    "/admin/orders/{order_id}/cancel",
    response_model=AdminSingleOrderResponse,
    summary="Admin — cancel order (broader status set)",
    description=(
        "Authorization: `orders.cancel`.  \n"
        "Adds `PACKED` and `READY_TO_DISPATCH` to customer-cancellable statuses."
    ),
)
async def admin_cancel_order(
    order_id: str,
    req: AdminCancelRequest,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = OrderService(db)
    order = await service.admin_cancel(order_id, req, actor_id=current_user.id)
    return AdminSingleOrderResponse(order=order)


# ===========================================================================
# ADMIN — add internal note
# ===========================================================================

@router.post(
    "/admin/orders/{order_id}/notes",
    response_model=AdminSingleOrderResponse,
    summary="Admin — add internal note to order",
    description=(
        "Authorization: `orders.manage`.  \n"
        "Body: `{ note: string }`. Appends to `internal_notes[]`; logs `NOTE_ADDED` activity."
    ),
)
async def admin_add_note(
    order_id: str,
    req: AddNoteRequest,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = OrderService(db)
    order = await service.add_note(
        order_id, req,
        actor_id=current_user.id,
        actor_name=current_user.full_name,
    )
    return AdminSingleOrderResponse(order=order)


# ===========================================================================
# ADMIN — apply validated status transition
# ===========================================================================

@router.post(
    "/admin/orders/{order_id}/status",
    response_model=AdminSingleOrderResponse,
    summary="Admin — apply validated status transition",
    description=(
        "Authorization: `orders.manage`.  \n"
        "Enforces `ORDER_TRANSITIONS` adjacency map.  \n"
        "Body: `{ status, note? }`."
    ),
)
async def admin_apply_status(
    order_id: str,
    req: ApplyStatusRequest,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = OrderService(db)
    order = await service.apply_status(order_id, req, actor_id=current_user.id)
    return AdminSingleOrderResponse(order=order)


# ===========================================================================
# ADMIN — force status (bypass adjacency — always audited)
# ===========================================================================

@router.post(
    "/admin/orders/{order_id}/force-status",
    response_model=AdminSingleOrderResponse,
    summary="Admin — force status transition (bypasses adjacency map)",
    description=(
        "Authorization: `orders.manage`.  \n"
        "**Bypasses `ORDER_TRANSITIONS`** — use only when manual recovery is required.  \n"
        "Body: `{ status, reason }` — `reason` is mandatory and always written to audit trail."
    ),
)
async def admin_force_status(
    order_id: str,
    req: ForceStatusRequest,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = OrderService(db)
    order = await service.force_status(order_id, req, actor_id=current_user.id)
    return AdminSingleOrderResponse(order=order)


# ===========================================================================
# ADMIN — invoice
# ===========================================================================

@router.get(
    "/admin/orders/{order_id}/invoice",
    response_model=InvoiceResponse,
    summary="Admin — get order invoice metadata",
    description=(
        "Authorization: `orders.view`.  \n"
        "Returns the invoice number and issue date **if one has been issued** "
        "(`available`). No invoice document is generated by this system: "
        "`documentAvailable` is always `false` and no download URL exists."
    ),
)
async def admin_get_invoice(
    order_id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = OrderService(db)
    data = await service.get_invoice(order_id)
    return InvoiceResponse(**data)


# ===========================================================================
# ADMIN RETURNS DESK — list
# ===========================================================================

@router.get(
    "/admin/returns",
    response_model=ReturnListResponse,
    summary="Admin — list all return requests",
    description="Authorization: `returns.view`.",
)
async def admin_list_returns(
    status_filter: Optional[str] = Query(None, alias="status"),
    order_id: Optional[str] = Query(None, alias="orderId"),
    customer_id: Optional[str] = Query(None, alias="customerId"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100, alias="pageSize"),
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = ReturnService(db)
    result = await service.list_returns(
        status=status_filter,
        order_id=order_id,
        customer_id=customer_id,
        page=page,
        page_size=page_size,
    )
    return ReturnListResponse(returns=result["returns"], total=result["total"])


# ===========================================================================
# ADMIN RETURNS DESK — get detail
# ===========================================================================

@router.get(
    "/admin/returns/{return_id}",
    response_model=SingleReturnResponse,
    summary="Admin — get return detail",
    description="Authorization: `returns.view`.",
)
async def admin_get_return(
    return_id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = ReturnService(db)
    ret = await service.get_return(return_id)
    return SingleReturnResponse(return_order=ret)


# ===========================================================================
# ADMIN RETURNS DESK — approve
# ===========================================================================

@router.post(
    "/admin/returns/{return_id}/approve",
    response_model=SingleReturnResponse,
    summary="Admin — approve return → APPROVED",
    description="Authorization: `returns.manage`.",
)
async def admin_approve_return(
    return_id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = ReturnService(db)
    ret = await service.approve_return(return_id, actor_id=current_user.id)
    return SingleReturnResponse(return_order=ret)


# ===========================================================================
# ADMIN RETURNS DESK — reject
# ===========================================================================

@router.post(
    "/admin/returns/{return_id}/reject",
    response_model=SingleReturnResponse,
    summary="Admin — reject return → REJECTED",
    description=(
        "Authorization: `returns.manage`.  \n"
        "Body: `{ reason, customerMessage? }`.  \n"
        "`reason` is an internal code; `customerMessage` is the customer-facing copy "
        "(auto-derived if omitted)."
    ),
)
async def admin_reject_return(
    return_id: str,
    req: ReturnRejectRequest,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = ReturnService(db)
    ret = await service.reject_return(return_id, req, actor_id=current_user.id)
    return SingleReturnResponse(return_order=ret)


# ===========================================================================
# ADMIN RETURNS DESK — schedule pickup
# ===========================================================================

@router.post(
    "/admin/returns/{return_id}/schedule-pickup",
    response_model=SingleReturnResponse,
    summary="Admin — schedule return pickup → PICKUP_SCHEDULED",
    description=(
        "Authorization: `returns.manage`.  \n"
        "Body: `{ scheduledAt, pickupAddress? }`."
    ),
)
async def admin_schedule_pickup(
    return_id: str,
    req: SchedulePickupRequest,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = ReturnService(db)
    ret = await service.schedule_pickup(return_id, req, actor_id=current_user.id)
    return SingleReturnResponse(return_order=ret)


# ===========================================================================
# ADMIN RETURNS DESK — receive
# ===========================================================================

@router.post(
    "/admin/returns/{return_id}/receive",
    response_model=SingleReturnResponse,
    summary="Admin — mark return received → RECEIVED",
    description=(
        "Authorization: `returns.manage`.  \n"
        "Body: `{ packageCondition, notes? }`."
    ),
)
async def admin_receive_return(
    return_id: str,
    req: ReceiveReturnRequest,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = ReturnService(db)
    ret = await service.receive_return(return_id, req, actor_id=current_user.id)
    return SingleReturnResponse(return_order=ret)


# ===========================================================================
# ADMIN RETURNS DESK — inspect
# ===========================================================================

@router.post(
    "/admin/returns/{return_id}/inspect",
    response_model=SingleReturnResponse,
    summary="Admin — inspect return → INSPECTED",
    description=(
        "Authorization: `returns.manage`.  \n"
        "Body: `{ inspectionCondition, notes? }`."
    ),
)
async def admin_inspect_return(
    return_id: str,
    req: InspectReturnRequest,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = ReturnService(db)
    ret = await service.inspect_return(return_id, req, actor_id=current_user.id)
    return SingleReturnResponse(return_order=ret)


# ===========================================================================
# ADMIN RETURNS DESK — initiate refund
# ===========================================================================

@router.post(
    "/admin/returns/{return_id}/refund/initiate",
    response_model=SingleReturnResponse,
    summary="Admin — initiate refund → REFUND_INITIATED",
    description="Authorization: `returns.manage`.",
)
async def admin_initiate_refund(
    return_id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = ReturnService(db)
    ret = await service.initiate_refund(return_id, actor_id=current_user.id)
    return SingleReturnResponse(return_order=ret)


# ===========================================================================
# ADMIN RETURNS DESK — complete refund
# ===========================================================================

@router.post(
    "/admin/returns/{return_id}/refund/complete",
    response_model=SingleReturnResponse,
    summary="Admin — complete refund → REFUNDED",
    description="Authorization: `returns.manage`.",
)
async def admin_complete_refund(
    return_id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = ReturnService(db)
    ret = await service.complete_refund(return_id, actor_id=current_user.id)
    return SingleReturnResponse(return_order=ret)
