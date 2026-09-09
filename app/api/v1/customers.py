"""
USERS section — Customer profile, preferences, sessions and admin customer management.

URL mapping  (API_CONTRACT.md → USERS):
  GET    /customers/me                            → get_me
  PATCH  /customers/me                            → update_profile
  PATCH  /customers/me/preferences               → update_preferences
  POST   /customers/me/sessions/revoke-others    → revoke_other_sessions
  GET    /admin/customers                         → admin_list_customers
  GET    /admin/customers/{customerId}            → admin_get_customer
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenException
from app.dependencies import get_current_customer, get_current_user, get_db, require_permission_for_user
from app.models.auth.user import UserModel
from app.schemas.customer.address import AddressResponse
from app.schemas.customer.customer import (
    AdminCustomerListResponse,
    AdminCustomerResponse,
    MeResponse,
    PreferencesResponse,
    PreferencesUpdate,
    ProfileResponse,
    ProfileUpdate,
    SessionSummary,
)
from app.services.customer.customer_service import CustomerService

router = APIRouter(tags=["Users — Customer Profile"])


# ──────────────────────────────────────────────────────────────────────────────
# GET /customers/me
# ──────────────────────────────────────────────────────────────────────────────

@router.get(
    "/customers/me",
    response_model=MeResponse,
    summary="Get current customer profile, addresses, preferences & sessions",
    description=(
        "Returns `{ profile, addresses[], preferences, security: { activeSessions[] } }`. "
        "Authorization: Customer session (Bearer token).\n\n"
        "**BACKEND_GAP (documented):** the access token carries no session id "
        "claim, so the caller's session cannot be identified — every "
        "`activeSessions[]` entry is returned with `isCurrent: false`. The "
        "frontend renders these as active sessions without a current-device "
        "badge. Remediation: mint a `sid` claim at issue time and pass it "
        "through (the service already accepts it)."
    ),
)
async def get_me(
    current_user: UserModel = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    service = CustomerService(db)
    user, profile, prefs, sessions = await service.get_me(current_user.id)

    profile_resp = ProfileResponse(
        id=user.id,
        first_name=profile.first_name,
        last_name=profile.last_name,
        email=user.email,
        phone=user.phone,
        date_of_birth=profile.date_of_birth,
        avatar=profile.avatar,
        loyalty_tier=profile.loyalty_tier,
        loyalty_points=profile.loyalty_points,
        created_at=user.created_at,
    )
    prefs_resp = PreferencesResponse.model_validate(prefs)
    addresses_resp = [AddressResponse.model_validate(a) for a in profile.addresses]
    sessions_resp = [s.model_dump(by_alias=True) for s in sessions]

    return MeResponse(
        profile=profile_resp,
        addresses=addresses_resp,
        preferences=prefs_resp,
        security={"activeSessions": sessions_resp},
    )


# ──────────────────────────────────────────────────────────────────────────────
# PATCH /customers/me
# ──────────────────────────────────────────────────────────────────────────────

@router.patch(
    "/customers/me",
    summary="Update customer profile",
    description=(
        "Accepts any subset of `{ firstName, lastName, email, phone, dateOfBirth, avatar }`. "
        "Authorization: Customer session."
    ),
)
async def update_profile(
    data: ProfileUpdate,
    current_user: UserModel = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    service = CustomerService(db)
    user, profile = await service.update_profile(current_user.id, data)
    return {
        "ok": True,
        "profile": ProfileResponse(
            id=user.id,
            first_name=profile.first_name,
            last_name=profile.last_name,
            email=user.email,
            phone=user.phone,
            date_of_birth=profile.date_of_birth,
            avatar=profile.avatar,
            loyalty_tier=profile.loyalty_tier,
            loyalty_points=profile.loyalty_points,
            created_at=user.created_at,
        ).model_dump(by_alias=True),
    }


# ──────────────────────────────────────────────────────────────────────────────
# PATCH /customers/me/preferences
# ──────────────────────────────────────────────────────────────────────────────

@router.patch(
    "/customers/me/preferences",
    summary="Update notification and communication preferences",
    description=(
        "Body: `{ emailNotifications, smsNotifications, promotionalUpdates, "
        "orderUpdates, stylingInvitations }` — all boolean, all optional. "
        "Authorization: Customer session."
    ),
)
async def update_preferences(
    data: PreferencesUpdate,
    current_user: UserModel = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    service = CustomerService(db)
    prefs = await service.update_preferences(current_user.id, data)
    return {
        "ok": True,
        "preferences": PreferencesResponse.model_validate(prefs).model_dump(by_alias=True),
    }


# ──────────────────────────────────────────────────────────────────────────────
# POST /customers/me/sessions/revoke-others
# ──────────────────────────────────────────────────────────────────────────────

@router.post(
    "/customers/me/sessions/revoke-others",
    status_code=status.HTTP_200_OK,
    summary="Sign out all other active sessions",
    description=(
        "Revokes active sessions for the customer. "
        "Authorization: Customer session.\n\n"
        "**BACKEND_GAP (documented):** the access token carries no session id "
        "claim, so the calling session cannot be excluded — this currently "
        "revokes **ALL** active sessions, including the one making the "
        "request (the caller is signed out everywhere). The frontend states "
        "this on the security page instead of promising 'other devices only'. "
        "Remediation: mint a `sid` claim at issue time and pass it through "
        "(the service already accepts the current-session argument)."
    ),
)
async def revoke_other_sessions(
    current_user: UserModel = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    service = CustomerService(db)
    revoked = await service.revoke_other_sessions(current_user.id)
    return {"ok": True, "revokedCount": revoked}


# ──────────────────────────────────────────────────────────────────────────────
# Admin: GET /admin/customers
# ──────────────────────────────────────────────────────────────────────────────

@router.get(
    "/admin/customers",
    response_model=AdminCustomerListResponse,
    summary="[Admin] List all registered customers",
    description=(
        "Authorization: `customers.view` permission required.  \n"
        "Returns customer records with derived `orderCount`, `lifetimeSpend`, "
        "and `addresses[]`."
    ),
)
async def admin_list_customers(
    q: Optional[str] = Query(None, description="Search by name, email, or phone"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100, alias="pageSize"),
    current_user: UserModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Permission check — admin or employee with customers.view
    if current_user.user_type not in ("admin", "employee"):
        raise ForbiddenException("customers.view permission required.")
    await require_permission_for_user(current_user, db, "customers.view")

    service = CustomerService(db)
    customers, total = await service.list_customers(q=q, page=page, page_size=page_size)
    return AdminCustomerListResponse(customers=customers, total=total)


# ──────────────────────────────────────────────────────────────────────────────
# Admin: GET /admin/customers/{customerId}
# ──────────────────────────────────────────────────────────────────────────────

@router.get(
    "/admin/customers/{customer_id}",
    response_model=AdminCustomerResponse,
    summary="[Admin] Get a single customer with full detail",
    description=(
        "Authorization: `customers.view` permission required.  \n"
        "Returns customer profile + addresses[] + derived stats."
    ),
)
async def admin_get_customer(
    customer_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.user_type not in ("admin", "employee"):
        raise ForbiddenException("customers.view permission required.")
    await require_permission_for_user(current_user, db, "customers.view")

    service = CustomerService(db)
    return await service.get_customer_detail(customer_id)
