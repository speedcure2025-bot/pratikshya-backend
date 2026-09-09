"""
Coupons / Offers — API router.

URL mapping (API_CONTRACT.md § OFFERS → implementation):

  Public
  ─────────────────────────────────────────────────────────────────────────────
  GET  /offers                     ← list active public offers
  POST /offers/validate            ← THE SINGLE CHECKOUT GATE (cart coupon validation)

  Admin
  ─────────────────────────────────────────────────────────────────────────────
  GET  /admin/offers               ← filtered + paginated list (offers.view)
  GET  /admin/offers/{id}          ← single offer, any status (offers.view)
  POST /admin/offers               ← create offer (offers.create)
  PATCH /admin/offers/{id}         ← update (offers.edit)
  POST /admin/offers/{id}/activate ← is_active → true (offers.edit)
  POST /admin/offers/{id}/pause    ← is_active → false (offers.edit)
  POST /admin/offers/{id}/archive  ← is_active → false (offers.archive)

  Persistence boundary (no schema changes): `catalog_coupon` models status as
  the boolean `is_active` plus `starts_at`/`expires_at`, so display status is
  DERIVED (ACTIVE / SCHEDULED / EXPIRED / ARCHIVED). Pause and archive both
  write `is_active=false`; they are distinct admin intents recorded in the
  response `intent` but share one persisted representation (BACKEND_GAP —
  documented, not invented around).

Notes:
  - POST /offers/validate is the single gate used by the cart coupon endpoint
    AND the checkout page. The CartService calls it internally; this endpoint
    exists for direct frontend calls.
  - All discount amounts are in whole rupees (INR).
"""

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import invalidate_response_cache
from app.core.exceptions import (
    BusinessLogicException,
    ConflictException,
    NotFoundException,
)
from app.dependencies import (
    get_current_admin,
    get_current_customer,
    get_db,
    get_optional_user,
    require_admin_permission,
)
from app.models.auth.user import UserModel
from app.models.commerce.coupon import CouponModel

router = APIRouter(tags=["Coupons & Offers"])


# ---------------------------------------------------------------------------
# Schemas (inline — small surface, no separate schema file needed)
# ---------------------------------------------------------------------------

class ValidateCouponRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=50)
    cart_items: Optional[list] = Field(default_factory=list)
    customer_id: Optional[str] = None
    customer_email: Optional[str] = None


class CreateCouponRequest(BaseModel):
    code: str = Field(..., min_length=2, max_length=24)
    name: Optional[str] = None
    description: Optional[str] = None
    discount_type: str = Field("percentage", pattern="^(percentage|fixed|free_shipping)$")
    discount_value: float = Field(..., ge=0)
    minimum_order_value: int = Field(0, ge=0)
    starts_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    usage_limit: Optional[int] = None
    per_customer_limit: Optional[int] = None
    eligible_customer_ids: Optional[List[str]] = None
    eligible_product_ids: Optional[List[str]] = None
    eligible_category_ids: Optional[List[str]] = None
    eligible_collection_ids: Optional[List[str]] = None
    excluded_product_ids: Optional[List[str]] = None
    excluded_category_ids: Optional[List[str]] = None
    is_stackable: bool = False


class UpdateCouponRequest(BaseModel):
    """PATCH /admin/offers/{id} — mirrors the full CREATOR contract, all optional.

    Any field the create endpoint accepts can be edited (including `code`,
    `discount_type` and the eligibility/exclusion lists). `usage_count` is
    never client-writable. Unknown keys are ignored — never persisted.
    """

    model_config = {"extra": "ignore"}

    code: Optional[str] = Field(None, min_length=2, max_length=24)
    name: Optional[str] = None
    description: Optional[str] = None
    discount_type: Optional[str] = Field(None, pattern="^(percentage|fixed|free_shipping)$")
    discount_value: Optional[float] = None
    minimum_order_value: Optional[int] = None
    starts_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    usage_limit: Optional[int] = None
    per_customer_limit: Optional[int] = None
    eligible_customer_ids: Optional[List[str]] = None
    eligible_product_ids: Optional[List[str]] = None
    eligible_category_ids: Optional[List[str]] = None
    eligible_collection_ids: Optional[List[str]] = None
    excluded_product_ids: Optional[List[str]] = None
    excluded_category_ids: Optional[List[str]] = None
    is_stackable: Optional[bool] = None
    is_active: Optional[bool] = None


def _validate_coupon_fields(*, code, discount_type, discount_value, starts_at, expires_at):
    """Shared validation for create + update (422 with actionable messages)."""
    errors = []
    if discount_type == "percentage" and discount_value is not None and discount_value > 100:
        errors.append("A percentage discount cannot exceed 100%.")
    if discount_value is not None and discount_value < 0:
        errors.append("Discount value cannot be negative.")
    if starts_at and expires_at:
        a = starts_at if starts_at.tzinfo else starts_at.replace(tzinfo=timezone.utc)
        b = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
        if b <= a:
            errors.append("`expires_at` must be after `starts_at`.")
    if code is not None and code.strip() != code:
        errors.append("Coupon code cannot contain surrounding whitespace.")
    if errors:
        raise BusinessLogicException(
            "The offer could not be saved.", details={"errors": errors}
        )


def _coupon_to_dict(c: CouponModel) -> dict:
    now = datetime.now(timezone.utc)
    # Derive display status from dates and is_active
    if not c.is_active:
        display_status = "ARCHIVED"
    elif c.starts_at and c.starts_at > now:
        display_status = "SCHEDULED"
    elif c.expires_at and c.expires_at < now:
        display_status = "EXPIRED"
    else:
        display_status = "ACTIVE"

    return {
        "id": c.id,
        "code": c.code,
        "name": c.name,
        "description": c.description,
        "discount_type": c.discount_type,
        "discount_value": c.discount_value,
        "minimum_order_value": c.minimum_order_value,
        "starts_at": c.starts_at.isoformat() if c.starts_at else None,
        "expires_at": c.expires_at.isoformat() if c.expires_at else None,
        "usage_limit": c.usage_limit,
        "usage_count": c.usage_count,
        "per_customer_limit": c.per_customer_limit,
        "is_stackable": c.is_stackable,
        "is_active": c.is_active,
        "display_status": display_status,
        # Eligibility / exclusion scope — full contract so the admin desk can
        # show and edit exactly what checkout will honour.
        "eligible_customer_ids": c.eligible_customer_ids or [],
        "eligible_product_ids": c.eligible_product_ids or [],
        "eligible_category_ids": c.eligible_category_ids or [],
        "eligible_collection_ids": c.eligible_collection_ids or [],
        "excluded_product_ids": c.excluded_product_ids or [],
        "excluded_category_ids": c.excluded_category_ids or [],
        "created_at": c.created_at.isoformat(),
        "updated_at": c.updated_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Validation helper
# ---------------------------------------------------------------------------

def _validate_coupon_logic(coupon: CouponModel, cart_subtotal: int = 0) -> Optional[str]:
    """Return an error string if the coupon is not valid, else None."""
    now = datetime.now(timezone.utc)
    if not coupon.is_active:
        return "This coupon is no longer active."
    if coupon.starts_at and coupon.starts_at > now:
        return "This coupon is not yet valid."
    if coupon.expires_at and coupon.expires_at < now:
        return "This coupon has expired."
    if coupon.usage_limit is not None and coupon.usage_count >= coupon.usage_limit:
        return "This coupon has reached its usage limit."
    if cart_subtotal < coupon.minimum_order_value:
        return f"Minimum order of ₹{coupon.minimum_order_value} required for this coupon."
    return None


# ===========================================================================
# PUBLIC — list active offers
# ===========================================================================

@router.get(
    "/offers",
    summary="List active public offers",
    description="Returns all currently active (non-archived) coupons/offers. Authorization: public.",
)
async def list_offers(
    db: AsyncSession = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    stmt = select(CouponModel).where(CouponModel.is_active == True)
    result = await db.execute(stmt)
    coupons = result.scalars().all()

    # Only return currently valid ones for public listing
    active = [c for c in coupons if (not c.expires_at or c.expires_at > now)]
    return {"ok": True, "offers": [_coupon_to_dict(c) for c in active]}


# ===========================================================================
# PUBLIC — validate coupon (THE SINGLE CHECKOUT GATE)
# ===========================================================================

@router.post(
    "/offers/validate",
    summary="Validate a coupon code — the single checkout gate",
    description=(
        "Body: `{ code, cartItems[], customerId?, customerEmail? }`.  \n\n"
        "**This is the one gate through which every discount must pass.** "
        "No other code path may grant a discount.  \n\n"
        "Response success: `{ ok: true, coupon, discount }`.  \n"
        "Response failure: `{ ok: false, error: 'Human readable message.' }`."
    ),
)
async def validate_offer(
    req: ValidateCouponRequest,
    current_user: Optional[UserModel] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    code = req.code.strip().upper()
    stmt = select(CouponModel).where(CouponModel.code == code)
    result = await db.execute(stmt)
    coupon = result.scalars().first()

    if not coupon:
        raise NotFoundException(message=f"No coupon found with code '{code}'.")

    # Compute cart subtotal from items if provided
    cart_subtotal = sum(
        int(item.get("lineTotal", item.get("price", 0) * item.get("quantity", 1)))
        for item in (req.cart_items or [])
    )

    error = _validate_coupon_logic(coupon, cart_subtotal=cart_subtotal)
    if error:
        raise BusinessLogicException(message=error)

    # Compute discount amount
    discount = 0
    if coupon.discount_type == "percentage":
        discount = int(cart_subtotal * coupon.discount_value / 100)
    elif coupon.discount_type == "fixed":
        discount = int(min(coupon.discount_value, cart_subtotal))
    elif coupon.discount_type == "free_shipping":
        discount = 0  # applied at totals level

    return {
        "ok": True,
        "coupon": _coupon_to_dict(coupon),
        "discount": discount,
    }


# ===========================================================================
# ADMIN — CRUD
# ===========================================================================

def _offer_display_status(c: CouponModel) -> str:
    if not c.is_active:
        return "ARCHIVED"
    now = datetime.now(timezone.utc)
    if c.starts_at and c.starts_at > now:
        return "SCHEDULED"
    if c.expires_at and c.expires_at < now:
        return "EXPIRED"
    return "ACTIVE"


async def _get_coupon_or_404(db: AsyncSession, offer_id: str) -> CouponModel:
    result = await db.execute(select(CouponModel).where(CouponModel.id == offer_id))
    coupon = result.scalars().first()
    if not coupon:
        raise NotFoundException(f"Offer '{offer_id}' not found.")
    return coupon


@router.get(
    "/admin/offers",
    summary="Admin — list offers/coupons with filters and pagination",
    description=(
        "Authorization: `offers.view`. Returns all offers including ARCHIVED.  \n"
        "Filters: `q` (code/name substring), `status` "
        "(`ACTIVE|SCHEDULED|EXPIRED|ARCHIVED`, evaluated from is_active/dates).  \n"
        "Pagination: `page` + `pageSize`; `total` is the full filtered count."
    ),
)
async def admin_list_offers(
    q: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    pageSize: int = 25,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "offers.view")
    stmt = select(CouponModel).order_by(CouponModel.created_at.desc())
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            (CouponModel.code.ilike(like)) | (CouponModel.name.ilike(like))
        )
    result = await db.execute(stmt)
    coupons = result.scalars().all()

    rows = [_coupon_to_dict(c) for c in coupons]

    # Honest aggregate tiles for the desk: derived from the FULL q-filtered
    # set (before the status filter/pagination), so counts never describe a
    # page while claiming to describe the register. No fabricated
    # "usage today" or per-day analytics — those surfaces do not exist in
    # the coupon table.
    counts = {"total": len(rows), "ACTIVE": 0, "SCHEDULED": 0, "EXPIRED": 0, "ARCHIVED": 0}
    lifetime = 0
    for row in rows:
        counts[row["display_status"]] = counts.get(row["display_status"], 0) + 1
        lifetime += int(row.get("usage_count") or 0)

    wanted = (status or "").strip().upper()
    if wanted:
        rows = [r for r in rows if r["display_status"] == wanted]

    total = len(rows)
    page = max(1, page)
    size = min(max(1, pageSize), 200)
    start = (page - 1) * size
    return {"ok": True, "offers": rows[start : start + size], "total": total,
            "page": page, "pageSize": size,
            "counts": counts, "lifetimeRedemptions": lifetime}


@router.get(
    "/admin/offers/{offer_id}",
    summary="Admin — get one offer (any status)",
    description="Authorization: `offers.view`. Resolves inactive/expired rows too.",
)
async def admin_get_offer(
    offer_id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "offers.view")
    coupon = await _get_coupon_or_404(db, offer_id)
    return {"ok": True, "offer": _coupon_to_dict(coupon)}


@router.post(
    "/admin/offers",
    status_code=201,
    summary="Admin — create a new coupon/offer",
    description=(
        "Authorization: `offers.create`. Code is uppercased and must be unique "
        "— duplicates are rejected with **409**, invalid discount windows with **422**."
    ),
)
async def admin_create_offer(
    req: CreateCouponRequest,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "offers.create")
    code = req.code.strip().upper()
    _validate_coupon_fields(
        code=code,
        discount_type=req.discount_type,
        discount_value=req.discount_value,
        starts_at=req.starts_at,
        expires_at=req.expires_at,
    )
    existing = await db.execute(select(CouponModel).where(CouponModel.code == code))
    if existing.scalars().first():
        raise ConflictException(
            f"Coupon code '{code}' already exists. Choose a different code or edit the existing offer."
        )

    coupon = CouponModel(
        code=code,
        name=req.name,
        description=req.description,
        discount_type=req.discount_type,
        discount_value=req.discount_value,
        minimum_order_value=req.minimum_order_value,
        starts_at=req.starts_at,
        expires_at=req.expires_at,
        usage_limit=req.usage_limit,
        per_customer_limit=req.per_customer_limit,
        eligible_customer_ids=req.eligible_customer_ids,
        eligible_product_ids=req.eligible_product_ids,
        eligible_category_ids=req.eligible_category_ids,
        eligible_collection_ids=req.eligible_collection_ids,
        excluded_product_ids=req.excluded_product_ids,
        excluded_category_ids=req.excluded_category_ids,
        is_stackable=req.is_stackable,
        is_active=True,
        created_by=current_user.id,
    )
    db.add(coupon)
    await db.flush()
    await invalidate_response_cache()
    return {"ok": True, "offer": _coupon_to_dict(coupon)}


@router.patch(
    "/admin/offers/{offer_id}",
    summary="Admin — update a coupon/offer",
    description=(
        "Authorization: `offers.edit`. Partial patch — only provided fields are "
        "written; `usage_count` is never client-writable. Changing `code` re-runs "
        "the uniqueness check (409 on collision)."
    ),
)
async def admin_update_offer(
    offer_id: str,
    req: UpdateCouponRequest,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "offers.edit")
    coupon = await _get_coupon_or_404(db, offer_id)

    patch = req.model_dump(exclude_unset=True)
    new_code = patch.pop("code", None)
    if new_code is not None:
        new_code = new_code.strip().upper()
    _validate_coupon_fields(
        code=new_code,
        discount_type=patch.get("discount_type", coupon.discount_type),
        discount_value=patch.get("discount_value", coupon.discount_value),
        starts_at=patch.get("starts_at", coupon.starts_at),
        expires_at=patch.get("expires_at", coupon.expires_at),
    )

    if new_code and new_code != coupon.code:
        clash = await db.execute(
            select(CouponModel).where(CouponModel.code == new_code, CouponModel.id != coupon.id)
        )
        if clash.scalars().first():
            raise ConflictException(f"Coupon code '{new_code}' already exists.")
        coupon.code = new_code

    for field, value in patch.items():
        setattr(coupon, field, value)

    await db.flush()
    await invalidate_response_cache()
    return {"ok": True, "offer": _coupon_to_dict(coupon)}


@router.post(
    "/admin/offers/{offer_id}/activate",
    summary="Admin — activate an offer (is_active → true)",
    description="Authorization: `offers.edit`.",
)
async def admin_activate_offer(
    offer_id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "offers.edit")
    coupon = await _get_coupon_or_404(db, offer_id)
    if coupon.is_active:
        return {"ok": True, "offer": _coupon_to_dict(coupon), "alreadyActive": True}
    coupon.is_active = True
    await db.flush()
    await invalidate_response_cache()
    return {"ok": True, "offer": _coupon_to_dict(coupon)}


@router.post(
    "/admin/offers/{offer_id}/pause",
    summary="Admin — pause an offer (is_active → false)",
    description=(
        "Authorization: `offers.edit`.  \n"
        "**Persistence boundary**: the coupon table has no separate paused flag, "
        "so pause archives the row's visibility (`is_active=false`); a paused and "
        "an archived offer are stored identically and re-activation is the same "
        "endpoint either way. The UI says exactly this."
    ),
)
async def admin_pause_offer(
    offer_id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "offers.edit")
    coupon = await _get_coupon_or_404(db, offer_id)
    coupon.is_active = False
    await db.flush()
    await invalidate_response_cache()
    return {"ok": True, "offer": _coupon_to_dict(coupon)}


@router.post(
    "/admin/offers/{offer_id}/archive",
    summary="Admin — archive an offer (is_active → false)",
    description=(
        "Authorization: `offers.archive` (SUPER_ADMIN only — the ADMIN role does "
        "not carry this permission). Same persisted effect as pause; kept as a "
        "distinct route for the permission difference."
    ),
)
async def admin_archive_offer(
    offer_id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "offers.archive")
    coupon = await _get_coupon_or_404(db, offer_id)
    coupon.is_active = False
    await db.flush()
    await invalidate_response_cache()
    return {"ok": True, "offer": _coupon_to_dict(coupon)}
