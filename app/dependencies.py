"""
FastAPI dependency functions.

Token authentication flow:
  1. Extract Bearer token from Authorization header (OAuth2PasswordBearer).
  2. Cryptographically verify + decode the JWT (decode_token).
  3. Check the token's `jti` against the blacklist — catches revoked
     tokens that haven't yet naturally expired (logout, password change).
  4. Load the UserModel from the DB and confirm status == ACTIVE.
"""

from typing import AsyncGenerator, Optional

from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.core.exceptions import ForbiddenException, UnauthorizedException
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.core.security import decode_token
from app.models.auth.user import UserModel
from app.models.rbac.role import RoleModel
from app.models.rbac.permission import PermissionModel
from app.models.rbac.role_permission import RolePermissionModel
from app.models.rbac.user_role import UserRoleModel

logger = get_logger("app.dependencies")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/customer/login", auto_error=False)


# ---------------------------------------------------------------------------
# Database session
# ---------------------------------------------------------------------------

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency providing async database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ---------------------------------------------------------------------------
# JWT auth with Redis blacklist check
# ---------------------------------------------------------------------------

async def get_current_user_claims(
    token: Optional[str] = Depends(oauth2_scheme),
) -> dict:
    """
    Decode JWT and validate claims.

    Steps:
      1. Reject if token is missing.
      2. Cryptographically verify the signature and expiry (decode_token).
      3. Confirm token_type == "access" (prevents refresh tokens being used here).
      4. Check Redis blacklist using the `jti` claim — covers revoked-before-expiry
         tokens (logout, password change, admin account suspension).
    """
    if not token:
        raise UnauthorizedException("Authentication token missing.")

    payload = decode_token(token)
    if not payload:
        logger.warning("Invalid or expired token presented")
        raise UnauthorizedException("Invalid or expired authentication token.")

    # Reject refresh tokens presented to access-token-only endpoints
    if payload.get("token_type") != "access":
        logger.warning("Non-access token type used on protected endpoint")
        raise UnauthorizedException("Access token required.")

    # Blacklist check — O(1) lookup by jti
    jti = payload.get("jti")
    if jti:
        redis = get_redis()
        is_blacklisted = await redis.exists(f"blacklist:access:{jti}")
        if is_blacklisted:
            logger.warning("Blacklisted token presented jti=%s", jti)
            raise UnauthorizedException("Token has been revoked. Please sign in again.")

    return payload


async def get_current_user(
    claims: dict = Depends(get_current_user_claims),
    db: AsyncSession = Depends(get_db),
) -> UserModel:
    """Get active UserModel for current authenticated request."""
    user_id = claims.get("sub")
    if not user_id:
        logger.error("Malformed JWT claims — sub missing")
        raise UnauthorizedException("Malformed token claims.")

    stmt = select(UserModel).where(UserModel.id == user_id)
    res = await db.execute(stmt)
    user = res.scalars().first()

    if not user:
        logger.warning("Authenticated user not found in DB user_id=%s", user_id)
        raise UnauthorizedException("Authenticated user account no longer exists.")

    if user.status != "ACTIVE":
        logger.warning("Inactive user attempted access user_id=%s status=%s", user_id, user.status)
        raise ForbiddenException(f"User account is {user.status.lower()}.")

    return user


async def get_optional_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> Optional[UserModel]:
    """Return the current user if a valid, non-blacklisted token is present, else None."""
    if not token:
        return None
    payload = decode_token(token)
    if not payload:
        return None
    if payload.get("token_type") != "access":
        return None

    jti = payload.get("jti")
    if jti:
        redis = get_redis()
        if await redis.exists(f"blacklist:access:{jti}"):
            return None

    user_id = payload.get("sub")
    if not user_id:
        return None
    stmt = select(UserModel).where(UserModel.id == user_id)
    res = await db.execute(stmt)
    user = res.scalars().first()
    if not user or user.status != "ACTIVE":
        return None
    return user


# ---------------------------------------------------------------------------
# Surface-scoped auth guards
# ---------------------------------------------------------------------------

async def get_current_customer(
    user: UserModel = Depends(get_current_user),
) -> UserModel:
    """Ensure current user is authenticated as a Customer."""
    if user.user_type != "customer":
        raise ForbiddenException("Customer authentication required.")
    return user


async def get_current_employee(
    user: UserModel = Depends(get_current_user),
) -> UserModel:
    """Ensure current user is authenticated as an Employee."""
    if user.user_type != "employee":
        raise ForbiddenException("Employee authentication required.")
    return user


async def get_current_admin(
    user: UserModel = Depends(get_current_user),
) -> UserModel:
    """Ensure current user is authenticated as an Admin."""
    if user.user_type != "admin":
        raise ForbiddenException("Admin authentication privileges required.")
    return user


# ---------------------------------------------------------------------------
# RBAC helpers
# ---------------------------------------------------------------------------

async def get_user_roles_and_permissions(
    user: UserModel,
    db: AsyncSession,
) -> tuple[list[str], list[str]]:
    """Return role names and permission codes for an already-authenticated user."""
    role_rows = (
        await db.execute(
            select(RoleModel.name)
            .join(UserRoleModel, UserRoleModel.role_id == RoleModel.id)
            .where(UserRoleModel.user_id == user.id)
        )
    ).scalars().all()
    roles = list(role_rows)

    permission_rows = (
        await db.execute(
            select(PermissionModel.code)
            .join(RolePermissionModel, RolePermissionModel.permission_id == PermissionModel.id)
            .join(RoleModel, RoleModel.id == RolePermissionModel.role_id)
            .join(UserRoleModel, UserRoleModel.role_id == RoleModel.id)
            .where(UserRoleModel.user_id == user.id)
        )
    ).scalars().all()
    permissions = set(permission_rows)

    # Reuse the existing built-in role vocabulary as a fallback for system
    # roles. This is not a second RBAC model; it mirrors the app's current
    # built-in roles when the DB role-permission rows are sparse.
    try:
        from app.api.v1.admin import BUILT_IN_ROLES
        for role in roles:
            for code in BUILT_IN_ROLES.get(role.upper(), {}).get("permissions", []):
                permissions.add(code)
    except Exception:
        logger.debug("Unable to load built-in RBAC fallback", exc_info=True)

    return roles, list(permissions)


async def require_permission_for_user(
    user: UserModel,
    db: AsyncSession,
    *required_permissions: str,
) -> None:
    """Raise 403 unless the user has every requested permission or wildcard."""
    roles, permissions = await get_user_roles_and_permissions(user, db)
    permission_set = set(permissions)
    if "SUPER_ADMIN" in roles or "*" in permission_set:
        return
    missing = [perm for perm in required_permissions if perm not in permission_set]
    if missing:
        raise ForbiddenException(f"Missing required permission: {', '.join(missing)}")


async def require_super_admin_user(user: UserModel, db: AsyncSession) -> None:
    """Raise 403 unless the authenticated admin has the SUPER_ADMIN role."""
    roles, _permissions = await get_user_roles_and_permissions(user, db)
    if "SUPER_ADMIN" not in roles:
        raise ForbiddenException("SUPER_ADMIN privileges required.")


async def require_admin_permission(
    user: UserModel,
    db: AsyncSession,
    *required_permissions: str,
) -> None:
    """
    Fine-grained permission check for ADMIN surfaces, layered on top of the
    `get_current_admin` surface guard (which already rejects customer and
    employee tokens with 403 — that isolation is what security depends on).

    Contract:
      • An admin carrying at least one role must actually hold every
        requested permission (SUPER_ADMIN role or a `*` permission passes).
      • An admin with NO role rows at all keeps the surface-level
        authorization the admin portal has always had. This is the documented
        compatibility path for databases where the RBAC directory has never
        been provisioned (the roles table can be empty even though admin
        accounts exist, because `register_admin` can only attach a role when
        a `roles` row exists). It is deliberately narrow: it only applies to
        `user_type == "admin"`, never to customer/employee surfaces, and it
        disappears as soon as roles are assigned to that account.

    This reuses the existing Phase-1 RBAC helpers (`users`/`roles`/
    `permissions` join models + the built-in role vocabulary fallback). It is
    NOT a second RBAC system.
    """
    roles, permissions = await get_user_roles_and_permissions(user, db)
    if not roles:
        # Provisioned-but-unassigned fallback: only admins reach this point
        # because every caller sits behind get_current_admin.
        return
    permission_set = set(permissions)
    if "SUPER_ADMIN" in roles or "*" in permission_set:
        return
    missing = [perm for perm in required_permissions if perm not in permission_set]
    if missing:
        raise ForbiddenException(
            f"You do not have permission to perform this action. "
            f"Missing required permission: {', '.join(missing)}"
        )
