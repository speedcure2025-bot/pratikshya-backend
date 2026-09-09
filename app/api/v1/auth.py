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
  POST /auth/admin/sign-up
  POST /auth/admin/sign-in         ← rate limited: 10/minute per IP
  POST /auth/admin/sign-out

  Shared
  ─────────────────────────────────────────────────────────
  POST /auth/refresh
  POST /auth/logout
  POST /auth/change-password
  GET  /auth/me

  OAuth
  ─────────────────────────────────────────────────────────
  POST /auth/oauth/google
  POST /auth/oauth/facebook
"""

from typing import Optional

from fastapi import APIRouter, Depends, Request, status
from fastapi.security.utils import get_authorization_scheme_param
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.middleware import limiter
from app.core.redis import get_redis
from app.core.security import decode_token
from app.dependencies import get_current_user, get_db
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


# ===========================================================================
# ADMIN — Registration / Sign-in / Sign-out
# ===========================================================================

@router.post(
    "/admin/sign-up",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an admin account",
    description=(
        "**Two allowed paths:**\n\n"
        "1. **Bootstrap** (no active admins exist) — anyone may call this. "
        "Gated by `ADMIN_BOOTSTRAP_SECRET` if that env var is set.\n\n"
        "2. **Privileged** (admins already exist) — requires a `SUPER_ADMIN` JWT "
        "or the bootstrap secret.\n\n"
        "Body: `{ full_name, email, phone?, password, confirmPassword?, adminSecret? }`\n\n"
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

    # Optionally resolve authenticated admin from bearer token (no 401 on missing)
    actor: Optional[UserModel] = None
    auth_header = request.headers.get("Authorization", "")
    scheme, token_value = get_authorization_scheme_param(auth_header)
    if scheme.lower() == "bearer" and token_value:
        payload = decode_token(token_value)
        jti = payload.get("jti") if payload else None
        revoked = bool(jti and await get_redis().exists(f"blacklist:access:{jti}"))
        if payload and not revoked and payload.get("token_type") == "access" and payload.get("user_type") == "admin":
            stmt = sa_select(UserModel).where(UserModel.id == payload.get("sub"))
            res = await db.execute(stmt)
            candidate = res.scalars().first()
            if candidate and candidate.user_type == "admin" and candidate.status == "ACTIVE":
                actor = candidate

    return await service.register_admin(
        req, ip_address=client_ip, user_agent=user_agent, actor=actor
    )


@router.post(
    "/admin/sign-in",
    response_model=TokenResponse,
    summary="Admin sign-in",
    description=(
        "Body: `{ adminId, password }`\n\n"
        "Returns `{ ok: true, admin: PublicAdmin }`.\n\n"
        "Blocked when `status === SUSPENDED`.\n\n"
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
    "/admin/sign-out",
    response_model=SignOutResponse,
    summary="Admin sign-out",
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
