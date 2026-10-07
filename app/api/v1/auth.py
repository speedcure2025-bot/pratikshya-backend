"""
Authentication & Identity — API router.

URL mapping (spec → implementation):

  Customer surface
  ─────────────────────────────────────────────────────────
  POST /auth/customer/sign-up      ← API_CONTRACT spec URL
  POST /auth/customer/sign-in      ← rate limited: 10/minute per IP
  POST /auth/customer/sign-out
  POST /auth/customer/forgot-password
  POST /auth/customer/reset-password

  Employee surface
  ─────────────────────────────────────────────────────────
  POST /auth/employee/sign-in      ← rate limited: 10/minute per IP
  POST /auth/employee/change-password
  POST /auth/employee/sign-out
  POST /auth/employee/refresh

  Admin surface
  ─────────────────────────────────────────────────────────
  POST /auth/admin/sign-up         ← kept as alias (see /auth/super-admin/sign-up)
  POST /auth/admin/sign-in         ← ADMIN only (rejects SUPER_ADMIN) — rate limited: 10/minute per IP
  POST /auth/admin/sign-out

  Super Admin surface (fully distinct from Admin)
  ─────────────────────────────────────────────────────────
  POST /auth/super-admin/sign-up   ← bootstrap endpoint (one-time only)
  POST /auth/super-admin/sign-in   ← SUPER_ADMIN only (rejects ADMIN) — rate limited: 10/minute per IP
  POST /auth/super-admin/sign-out  ← SUPER_ADMIN only
  POST /auth/super-admin/refresh   ← SUPER_ADMIN only
  POST /auth/super-admin/change-password ← SUPER_ADMIN only
  GET  /auth/super-admin/me        ← SUPER_ADMIN only

  Unified staff login (all four account levels — the /login page calls this)
  ─────────────────────────────────────────────────────────
  POST /auth/staff/sign-in     ← identifier = email | phone | PF-code; the
                                  backend resolves the account level and
                                  issues the matching admin/employee surface
                                  response. The per-surface employee/admin
                                  sign-ins above stay live as compatibility
                                  endpoints for existing callers — there is
                                  exactly ONE authentication service behind
                                  all of them (AuthService).

  Shared
  ─────────────────────────────────────────────────────────
  POST /auth/refresh
  POST /auth/logout
  POST /auth/change-password
  GET  /auth/me
  PATCH /auth/me                   staff self-service profile (not customers)

  OAuth
  ─────────────────────────────────────────────────────────
  POST /auth/oauth/google
  POST /auth/oauth/facebook
"""

from typing import Optional

from fastapi import APIRouter, Depends, Request, status
from fastapi.security.utils import get_authorization_scheme_param
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.middleware import limiter
from app.dependencies import get_current_user, get_current_super_admin, get_db
from app.models.auth.user import UserModel
from app.schemas.auth.login import (
    AdminLoginRequest,
    AdminRegisterRequest,
    ChangePasswordRequest,
    CustomerLoginRequest,
    CustomerRegisterRequest,
    EmployeeLoginRequest,
    ForgotPasswordRequest,
    RefreshTokenRequest,
    ResetPasswordRequest,
    StaffLoginRequest,
    StaffProfileUpdateRequest,
)
from app.schemas.auth.oauth import GoogleOAuthRequest, FacebookOAuthRequest
from app.schemas.auth.token import (
    ForgotPasswordResponse,
    ResetPasswordResponse,
    SignOutResponse,
    TokenResponse,
    UserDTO,
)
from app.services.auth.auth_service import AuthService
from app.services.auth.oauth_service import OAuthService

router = APIRouter(prefix="/auth", tags=["Authentication & Identity"])

# Rate limit applied to all login endpoints: 10 attempts per minute per IP
_LOGIN_LIMIT = "10/minute"


# ===========================================================================
# CUSTOMER — Registration
# ===========================================================================

@router.post(
    "/customer/sign-up",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register new customer account",
    description=(
        "Body: `{ firstName, lastName, email, phone?, password, dateOfBirth? }`\n\n"
        "Also accepts `full_name` for backward compatibility.\n\n"
        "On success returns `{ ok: true, user: Customer }` and signs the user in immediately."
    ),
)
async def sign_up_customer(
    req: CustomerRegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    return await service.register_customer(req, ip_address=client_ip, user_agent=user_agent)


# ===========================================================================
# CUSTOMER — Sign-in / Sign-out
# ===========================================================================

@router.post(
    "/customer/sign-in",
    response_model=TokenResponse,
    summary="Customer sign-in",
    description=(
        "Body: `{ identifier: string (email OR phone), password, remember?: boolean }`\n\n"
        "Returns `{ ok: true, user: Customer }`.\n\n"
        "Rate limited to 10 attempts per minute per IP address."
    ),
)
@limiter.limit(_LOGIN_LIMIT)
async def sign_in_customer(
    req: CustomerLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    return await service.login_customer(req, ip_address=client_ip, user_agent=user_agent)


@router.post(
    "/customer/sign-out",
    response_model=SignOutResponse,
    summary="Customer sign-out",
    description=(
        "Immediately revokes the access token (blacklisted in-process) and all "
        "active refresh-token sessions. Returns `{ ok: true }`."
    ),
)
async def sign_out_customer(
    request: Request,
    current_user: UserModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    access_token = _extract_bearer(request)
    await service.logout(current_user.id, access_token=access_token)
    return SignOutResponse()


# ===========================================================================
# CUSTOMER — Forgot / Reset Password
# ===========================================================================

@router.post(
    "/customer/forgot-password",
    response_model=ForgotPasswordResponse,
    summary="Request customer password reset",
    description=(
        "Body: `{ identifier: string }` — registered email or phone.\n\n"
        "Always returns `{ ok: true, message }` regardless of whether the account "
        "exists (prevents account enumeration)."
    ),
)
async def customer_forgot_password(
    req: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    message = await service.forgot_password(req)
    return ForgotPasswordResponse(message=message)


@router.post(
    "/customer/reset-password",
    response_model=ResetPasswordResponse,
    summary="Reset customer password using token",
    description=(
        "Body: `{ userId, token, newPassword, confirmPassword }`\n\n"
        "Verifies the reset token stored in the cache, updates the password, "
        "and invalidates all sessions."
    ),
)
async def customer_reset_password(
    req: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    # Use the full implementation that requires user_id in the request
    await service.reset_password_with_user_id(
        user_id=req.userId,
        token=req.token,
        new_password=req.newPassword,
    )
    return ResetPasswordResponse()


# ===========================================================================
# EMPLOYEE — Sign-in / Change-password / Sign-out / Refresh
# ===========================================================================

@router.post(
    "/employee/sign-in",
    response_model=TokenResponse,
    summary="Employee sign-in",
    description=(
        "Body: `{ employeeId: 'PF-<PREFIX>-#####', password }`\n\n"
        "Returns `{ ok: true, employee: PublicEmployee, mustChangePassword: boolean }`.\n\n"
        "Blocked when `status ∈ {SUSPENDED, INACTIVE}`.\n\n"
        "Rate limited to 10 attempts per minute per IP address."
    ),
)
@limiter.limit(_LOGIN_LIMIT)
async def sign_in_employee(
    req: EmployeeLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    return await service.login_employee(req, ip_address=client_ip, user_agent=user_agent)


@router.post(
    "/employee/change-password",
    summary="Employee change password",
    description=(
        "Body: `{ currentPassword, newPassword, confirmPassword }`\n\n"
        "Clears the `force_password_change` flag, revokes all sessions, "
        "and blacklists the current access token.\n\n"
        "Returns `{ ok: true }`."
    ),
)
async def employee_change_password(
    req: ChangePasswordRequest,
    request: Request,
    current_user: UserModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    access_token = _extract_bearer(request)
    await service.change_password(
        current_user.id,
        req.old_password,
        req.new_password,
        access_token=access_token,
    )
    return {"ok": True, "message": "Password updated successfully."}


@router.post(
    "/employee/sign-out",
    response_model=SignOutResponse,
    summary="Employee sign-out",
)
async def sign_out_employee(
    request: Request,
    current_user: UserModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    access_token = _extract_bearer(request)
    await service.logout(current_user.id, access_token=access_token)
    return SignOutResponse()


@router.post(
    "/employee/refresh",
    response_model=TokenResponse,
    summary="Employee token refresh",
    description=(
        "Body: `{ refresh_token }`\n\n"
        "Rotates the refresh token (old one is blacklisted) and issues a new "
        "access token, re-reading the employee's current roles."
    ),
)
async def employee_refresh_token(
    req: RefreshTokenRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    current_access_token = _extract_bearer(request)
    return await service.refresh_access_token(
        req.refresh_token, current_access_token=current_access_token
    )


@router.post(
    "/staff/sign-in",
    response_model=TokenResponse,
    summary="Unified staff sign-in (SUPER_ADMIN / ADMIN / SUPER_EMPLOYEE / EMPLOYEE)",
    description=(
        "Body: `{ identifier: 'email | phone | PF-<PREFIX>-#####', password }`.\n\n"
        "The ONE canonical login for all four staff account levels. The backend\n"
        "authenticates the credential, resolves the account level from the user\n"
        "row and returns the surface-specific payload (`admin` for the Admin\n"
        "workspace levels, `employee` for the Employee workspace levels) with\n"
        "`account_level` / `workspace` on the DTO so the frontend can route.\n\n"
        "Customer storefront sign-in remains `/auth/customer/sign-in`; the\n"
        "legacy per-surface staff sign-ins remain as compatibility aliases.\n\n"
        "Rate limited to 10 attempts per minute per IP address."
    ),
)
@limiter.limit(_LOGIN_LIMIT)
async def sign_in_staff(
    req: StaffLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    # DESIGN NOTE: This unified endpoint intentionally accepts ALL four staff
    # account levels (SUPER_ADMIN, ADMIN, SUPER_EMPLOYEE, EMPLOYEE) — it is
    # the canonical /login page entry point.  The dedicated per-surface
    # endpoints (/auth/admin/sign-in, /auth/super-admin/sign-in) enforce
    # level-specific routing for clients that require it; this endpoint is
    # the intentional escape hatch for a single login form that serves all
    # staff.  The returned `account_level` / `workspace` fields on the DTO
    # are what the frontend uses to route the session — never accept those
    # values from the client.
    return await service.sign_in_staff(req, ip_address=client_ip, user_agent=user_agent)


# ===========================================================================
# ADMIN — Registration / Sign-in / Sign-out
# ===========================================================================

@router.post(
    "/admin/sign-up",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Bootstrap the SUPER_ADMIN account (alias — prefer /auth/super-admin/sign-up)",
    description=(
        "**Alias for `POST /auth/super-admin/sign-up`** — kept for backwards compatibility.\n\n"
        "**Bootstrap only — creates the single `SUPER_ADMIN` account for the system.**\n\n"
        "This endpoint is available **only when no active admin account exists yet**. "
        "Once the SUPER_ADMIN has been bootstrapped it is permanently closed; any "
        "subsequent call returns `403`.\n\n"
        "To create `ADMIN`, `SUPER_EMPLOYEE`, or `EMPLOYEE` accounts use "
        "`POST /admin/employees` instead.\n\n"
        "Body: `{ full_name, email, phone?, password, confirmPassword?, adminSecret? }`\n\n"
        "Gated by `ADMIN_BOOTSTRAP_SECRET` if that env var is set.\n\n"
        "Returns `{ ok: true, admin: PublicAdmin }`."
    ),
)
async def sign_up_admin(
    req: AdminRegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    return await service.register_admin(req, ip_address=client_ip, user_agent=user_agent)


@router.post(
    "/admin/sign-in",
    response_model=TokenResponse,
    summary="Admin sign-in (ADMIN level only)",
    description=(
        "Body: `{ adminId, password }`\n\n"
        "Returns `{ ok: true, admin: PublicAdmin }`.\n\n"
        "Blocked when `status === SUSPENDED`.\n\n"
        "**ADMIN-level accounts only.** SUPER_ADMIN accounts must use "
        "`POST /auth/super-admin/sign-in`.\n\n"
        "Rate limited to 10 attempts per minute per IP address."
    ),
)
@limiter.limit(_LOGIN_LIMIT)
async def sign_in_admin(
    req: AdminLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    return await service.login_admin(req, ip_address=client_ip, user_agent=user_agent)


@router.post(
    "/super-admin/sign-in",
    response_model=TokenResponse,
    summary="Super Admin sign-in (SUPER_ADMIN level only)",
    description=(
        "Body: `{ adminId, password }`\n\n"
        "Returns `{ ok: true, admin: PublicAdmin }`.\n\n"
        "Blocked when `status === SUSPENDED`.\n\n"
        "**SUPER_ADMIN accounts only.** ADMIN accounts must use "
        "`POST /auth/admin/sign-in`.\n\n"
        "Rate limited to 10 attempts per minute per IP address."
    ),
)
@limiter.limit(_LOGIN_LIMIT)
async def sign_in_super_admin(
    req: AdminLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    return await service.login_super_admin(req, ip_address=client_ip, user_agent=user_agent)


@router.post(
    "/admin/sign-out",
    response_model=SignOutResponse,
    summary="Admin sign-out (ADMIN level only)",
)
async def sign_out_admin(
    request: Request,
    current_user: UserModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    access_token = _extract_bearer(request)
    await service.logout(current_user.id, access_token=access_token)
    return SignOutResponse()


# ===========================================================================
# SUPER ADMIN — Dedicated auth endpoints (distinct from Admin)
# ===========================================================================

@router.post(
    "/super-admin/sign-up",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Bootstrap the SUPER_ADMIN account (one-time only)",
    description=(
        "**Bootstrap only — creates the single `SUPER_ADMIN` account for the system.**\n\n"
        "Available **only when no active admin account exists yet**. "
        "Once bootstrapped it is permanently closed.\n\n"
        "Body: `{ full_name, email, phone?, password, confirmPassword?, adminSecret? }`\n\n"
        "Gated by `ADMIN_BOOTSTRAP_SECRET` if that env var is set.\n\n"
        "Returns `{ ok: true, admin: PublicAdmin }`."
    ),
)
async def super_admin_sign_up(
    req: AdminRegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    return await service.register_admin(req, ip_address=client_ip, user_agent=user_agent)


@router.post(
    "/super-admin/sign-out",
    response_model=SignOutResponse,
    summary="Super Admin sign-out (SUPER_ADMIN only)",
    description=(
        "Revokes the access token and all active sessions for the signed-in "
        "SUPER_ADMIN account. Plain ADMIN tokens receive 403."
    ),
)
async def sign_out_super_admin(
    request: Request,
    current_user: UserModel = Depends(get_current_super_admin),
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    access_token = _extract_bearer(request)
    await service.logout(current_user.id, access_token=access_token)
    return SignOutResponse()


@router.post(
    "/super-admin/refresh",
    response_model=TokenResponse,
    summary="Super Admin token refresh (SUPER_ADMIN only)",
    description=(
        "Body: `{ refresh_token }`\n\n"
        "Rotates the refresh token (old one is blacklisted) and issues a new "
        "access token. Rejects tokens not belonging to a SUPER_ADMIN account."
    ),
)
async def super_admin_refresh_token(
    req: RefreshTokenRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    from app.core.security import decode_token as _decode
    from app.core.rbac import ACCOUNT_LEVEL_SUPER_ADMIN
    from app.core.exceptions import ForbiddenException as _Forbidden

    # Validate the refresh token's account_level claim before issuing new tokens.
    # Full rotation happens inside refresh_access_token; this pre-check keeps
    # SUPER_ADMIN and ADMIN refresh flows strictly separated.
    token_payload = _decode(req.refresh_token)
    if not token_payload or token_payload.get("user_type") != "admin":
        raise _Forbidden("Super Admin refresh token required.")

    service = AuthService(db)
    current_access_token = _extract_bearer(request)
    token_response = await service.refresh_access_token(
        req.refresh_token, current_access_token=current_access_token
    )

    # After rotation, verify the new token's user is actually SUPER_ADMIN.
    from app.dependencies import resolve_account_level, get_user_roles_and_permissions
    from sqlalchemy import select as _select

    from app.models.auth.user import UserModel as _UserModel
    user_id = token_payload.get("sub")
    res = await db.execute(_select(_UserModel).where(_UserModel.id == user_id))
    user = res.scalars().first()
    if not user:
        raise _Forbidden("Super Admin refresh token required.")
    roles, _ = await get_user_roles_and_permissions(user, db)
    if resolve_account_level(user, roles) != ACCOUNT_LEVEL_SUPER_ADMIN:
        raise _Forbidden("Super Admin refresh token required.")

    return token_response


@router.post(
    "/super-admin/change-password",
    summary="Super Admin change password (SUPER_ADMIN only)",
    description=(
        "Body: `{ currentPassword, newPassword, confirmPassword }`\n\n"
        "Clears the `force_password_change` flag, revokes all sessions, "
        "and blacklists the current access token.\n\n"
        "Plain ADMIN tokens receive 403."
    ),
)
async def super_admin_change_password(
    req: ChangePasswordRequest,
    request: Request,
    current_user: UserModel = Depends(get_current_super_admin),
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    access_token = _extract_bearer(request)
    await service.change_password(
        current_user.id,
        req.old_password,
        req.new_password,
        access_token=access_token,
    )
    return {"ok": True, "message": "Password updated successfully."}


@router.get(
    "/super-admin/me",
    response_model=UserDTO,
    summary="Get current Super Admin profile DTO (SUPER_ADMIN only)",
    description=(
        "Returns the identity DTO for the signed-in SUPER_ADMIN account. "
        "Plain ADMIN tokens receive 403."
    ),
)
async def super_admin_get_me(
    current_user: UserModel = Depends(get_current_super_admin),
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    roles, permissions = await service._get_user_roles_and_permissions(current_user.id)
    return await service._build_user_dto(current_user, roles, permissions)


# ===========================================================================
# SHARED — Refresh / Logout / Change-password / Me
# ===========================================================================

@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Exchange refresh token for a new access token (all surfaces)",
    description=(
        "Body: `{ refresh_token }`.\n\n"
        "Rotates the refresh token — the old one is blacklisted immediately."
    ),
)
async def refresh_token(
    req: RefreshTokenRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    current_access_token = _extract_bearer(request)
    return await service.refresh_access_token(
        req.refresh_token, current_access_token=current_access_token
    )


@router.post(
    "/logout",
    response_model=SignOutResponse,
    summary="Revoke active sessions and logout (all surfaces)",
)
async def logout(
    request: Request,
    current_user: UserModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    access_token = _extract_bearer(request)
    await service.logout(current_user.id, access_token=access_token)
    return SignOutResponse()


@router.post(
    "/change-password",
    summary="Change account password (all surfaces)",
    description=(
        "Body: `{ currentPassword, newPassword, confirmPassword }` (camelCase)\n\n"
        "Also accepts `{ old_password, new_password }` (snake_case).\n\n"
        "Revokes all sessions and blacklists the current access token on success."
    ),
)
async def change_password(
    req: ChangePasswordRequest,
    request: Request,
    current_user: UserModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    access_token = _extract_bearer(request)
    await service.change_password(
        current_user.id,
        req.old_password,
        req.new_password,
        access_token=access_token,
    )
    return {"ok": True, "message": "Password updated successfully."}


@router.get(
    "/me",
    response_model=UserDTO,
    summary="Get current authenticated user profile DTO",
)
async def get_me(
    current_user: UserModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    roles, permissions = await service._get_user_roles_and_permissions(current_user.id)
    return await service._build_user_dto(current_user, roles, permissions)


@router.patch(
    "/me",
    response_model=UserDTO,
    summary="Update current staff profile (name, phone, email, title)",
    description=(
        "Staff self-service (SUPER_ADMIN / ADMIN / SUPER_EMPLOYEE / EMPLOYEE). "
        "Persists display name, phone, email and title/designation on the "
        "signed-in user. Role, account level and employee code cannot be "
        "changed here. Customers use PATCH /customers/me."
    ),
)
async def update_me(
    req: StaffProfileUpdateRequest,
    current_user: UserModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    user = await service.update_own_profile(current_user, req)
    roles, permissions = await service._get_user_roles_and_permissions(user.id)
    return await service._build_user_dto(user, roles, permissions)


# ===========================================================================
# OAuth / Social Login
# ===========================================================================

@router.post(
    "/oauth/google",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Sign in / register with Google",
    description=(
        "Pass the Google ID token from the Google Identity SDK.\n\n"
        "The backend verifies the token, then finds or creates the customer account, "
        "and returns the same JWT pair as a regular sign-in."
    ),
)
async def oauth_google_login(
    req: GoogleOAuthRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    service = OAuthService(db)
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    return await service.google_login(req, ip_address=client_ip, user_agent=user_agent)


@router.post(
    "/oauth/facebook",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Sign in / register with Facebook",
    description=(
        "Pass the Facebook user access token from the Facebook Login SDK.\n\n"
        "The backend verifies the token via the Graph API, then finds or creates "
        "the customer account, and returns the same JWT pair as a regular sign-in."
    ),
)
async def oauth_facebook_login(
    req: FacebookOAuthRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    service = OAuthService(db)
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    return await service.facebook_login(req, ip_address=client_ip, user_agent=user_agent)


@router.get(
    "/oauth/config",
    status_code=status.HTTP_200_OK,
    summary="Get public OAuth client configurations",
)
async def get_oauth_config():
    """Return public client IDs for Google and Facebook OAuth."""
    return {
        "google_client_id": settings.GOOGLE_CLIENT_ID or "",
        "facebook_app_id": settings.FACEBOOK_APP_ID or "",
    }


# ===========================================================================
# Internal helper
# ===========================================================================

def _extract_bearer(request: Request) -> Optional[str]:
    """Extract the raw Bearer token string from the Authorization header, or None."""
    auth_header = request.headers.get("Authorization", "")
    scheme, credentials = get_authorization_scheme_param(auth_header)
    if scheme.lower() == "bearer" and credentials:
        return credentials
    return None
