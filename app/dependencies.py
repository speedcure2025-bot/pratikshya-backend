"""
FastAPI dependency functions.

Token authentication flow:
  1. Extract Bearer token from Authorization header (OAuth2PasswordBearer).
  2. Cryptographically verify + decode the JWT (decode_token).
  3. Check the token's `jti` against the blacklist — catches revoked
     tokens that haven't yet naturally expired (logout, password change).
  4. Load the UserModel from the DB and confirm status == ACTIVE.
"""

import json
from typing import AsyncGenerator, Optional, Sequence

from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import AsyncSessionLocal
from app.core.exceptions import ForbiddenException, UnauthorizedException
from app.core.logging import get_logger
from app.core.rbac import (
    ACCOUNT_LEVEL_SUPER_ADMIN,
    ACCOUNT_LEVEL_SUPER_EMPLOYEE,
    derive_account_level,
    expand_effective_permissions,
    resolve_stored_grants,
    with_employee_self_service,
)
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

    stmt = (
        select(UserModel)
        .where(UserModel.id == user_id)
        # Eager-load the 1:1 employee profile so employee-domain endpoints
        # (attendance / leave / performance) can read ``user.employee_profile``
        # without triggering a synchronous lazy-load — which would attempt IO
        # outside the async session and raise ``MissingGreenlet``.
        .options(selectinload(UserModel.employee_profile))
    )
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
    """Ensure current user is authenticated as an Admin.

    Admin-workspace surfaces stay exclusive to ``user_type == "admin"``
    (SUPER_ADMIN / ADMIN account levels); employees and customers get 403.
    """
    if user.user_type != "admin":
        raise ForbiddenException("Admin authentication privileges required.")
    return user


async def get_current_account_manager(
    user: UserModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserModel:
    """
    Surface guard for account-management operations (People domain).

    Admits workspace admins (SUPER_ADMIN / ADMIN) and employee-domain accounts
    at SUPER_EMPLOYEE level — the four-level matrix in `app.core.rbac` then
    decides which levels they may create/manage, and the capability checks
    decide the operations. Normal EMPLOYEE accounts are denied here: employee
    accounts carry no account-creation authority (§6/§11).
    """
    if user.user_type == "admin":
        return user
    if user.user_type == "employee":
        roles, _permissions = await get_user_roles_and_permissions(user, db)
        if resolve_account_level(user, roles) == ACCOUNT_LEVEL_SUPER_EMPLOYEE:
            return user
    raise ForbiddenException("Account management requires an Admin or Super Employee account.")


# ---------------------------------------------------------------------------
# RBAC helpers
# ---------------------------------------------------------------------------

# Shared short-lived resolution cache for roles/permissions. The same key is
# used by AuthService and invalidated on role/permission changes, so a guarded
# request never pays for repeated role/permission joins (§21 — bounded single
# lookup, no N+1, no new infrastructure: the existing in-process LRU store).
RBAC_CACHE_PREFIX = "rbac:"
_RBAC_CACHE_TTL_SECONDS = 300


async def get_user_roles_and_permissions(
    user: UserModel,
    db: AsyncSession,
) -> tuple[list[str], list[str]]:
    """
    Return (role names, EFFECTIVE permission codes) for an authenticated user.

    Effective codes are the union of every granted permission and the canonical
    capability model produced by ``app.core.rbac.expand_effective_permissions``
    — old granular grants and new capability grants therefore both satisfy
    either vocabulary at every guard. Resolution is cached in-process for a
    bounded TTL and invalidated whenever roles/permissions change.
    """
    redis = get_redis()
    cache_key = f"{RBAC_CACHE_PREFIX}{user.id}"
    try:
        cached = await redis.get(cache_key)
        if cached:
            data = json.loads(cached)
            roles = list(data["roles"])
            # Union on cache-hit too so a pre-fix TTL entry cannot blank
            # /employee for up to five minutes after deploy.
            perms = with_employee_self_service(
                getattr(user, "user_type", None), set(data["permissions"])
            )
            return roles, sorted(perms)
    except Exception:  # cache is an optimization, never an authority
        logger.debug("RBAC cache read failed", exc_info=True)

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
    permissions = resolve_stored_grants(
        account_level=resolve_account_level(user, roles),
        roles=roles,
        permission_mode=getattr(user, "permission_mode", None),
        custom_permissions=getattr(user, "custom_permissions", None),
        role_permission_codes=permission_rows,
    )

    permissions = expand_effective_permissions(permissions)
    permissions = with_employee_self_service(getattr(user, "user_type", None), permissions)

    try:
        await redis.setex(
            cache_key,
            _RBAC_CACHE_TTL_SECONDS,
            json.dumps({"roles": roles, "permissions": sorted(permissions)}),
        )
    except Exception:
        logger.debug("RBAC cache write failed", exc_info=True)

    return roles, sorted(permissions)


async def invalidate_rbac_cache(user_id: str) -> None:
    """Drop the cached role/permission resolution for a user (post-change)."""
    try:
        await get_redis().delete(f"{RBAC_CACHE_PREFIX}{user_id}")
    except Exception:
        logger.debug("RBAC cache invalidation failed for %s", user_id, exc_info=True)


def resolve_account_level(user: UserModel, roles: list[str] | None = None) -> Optional[str]:
    """Authoritative account level: explicit column, else deterministic derivation."""
    level = getattr(user, "account_level", None)
    if level:
        return str(level).upper()
    return derive_account_level(getattr(user, "user_type", None), roles)


async def get_user_account_level(user: UserModel, db: AsyncSession) -> Optional[str]:
    if getattr(user, "account_level", None):
        return str(user.account_level).upper()
    roles, _permissions = await get_user_roles_and_permissions(user, db)
    return derive_account_level(user.user_type, roles)


async def _audit_permission_denial(user: UserModel, missing: Sequence[str], surface: str) -> None:
    """Central ACCESS_DENIED diary write for capability refusals.

    One writer (app.services.audit.audit_service) on a detached session: the
    caller's transaction is about to roll back with the 403, so the entry is
    committed independently. Failures are swallowed inside record_detached —
    an audit problem must never mask the original 403.
    """
    if not isinstance(user, UserModel):  # test doubles / non-persistent callers
        return
    from app.services.audit.audit_service import record_detached

    try:
        await record_detached(
            action="ACCESS_DENIED",
            actor_id=user.id,
            summary=f"{surface}: {getattr(user, 'full_name', user.id)} denied — missing {', '.join(missing)}",
            details={"surface": surface, "missing": list(missing)},
        )
    except Exception:  # noqa: BLE001 — an audit failure never masks the 403
        import logging

        logging.getLogger("pfv.audit").exception("ACCESS_DENIED diary write failed")


async def require_permission_for_user(
    user: UserModel,
    db: AsyncSession,
    *required_permissions: str,
) -> None:
    """Raise 403 unless the user has every requested permission or wildcard."""
    roles, permissions = await get_user_roles_and_permissions(user, db)
    permission_set = set(permissions)
    if resolve_account_level(user, roles) == ACCOUNT_LEVEL_SUPER_ADMIN or "*" in permission_set:
        return
    missing = [perm for perm in required_permissions if perm not in permission_set]
    if missing:
        await _audit_permission_denial(user, missing, "staff-permission")
        raise ForbiddenException(f"Missing required permission: {', '.join(missing)}")


async def require_super_admin_user(user: UserModel, db: AsyncSession) -> None:
    """Raise 403 unless the authenticated admin is at SUPER_ADMIN level.

    Recognises both the canonical account level and the legacy SUPER_ADMIN
    role row so pre-migration databases keep working unchanged.
    """
    roles, _permissions = await get_user_roles_and_permissions(user, db)
    if resolve_account_level(user, roles) == ACCOUNT_LEVEL_SUPER_ADMIN or "SUPER_ADMIN" in roles:
        return
    raise ForbiddenException("SUPER_ADMIN privileges required.")


async def require_staff_permission(user: UserModel, db: AsyncSession, *required_permissions: str) -> None:
    """
    Capability check valid for BOTH workspaces (admin handlers and employee
    actors on People-domain routes). Admins keep the hardened
    `require_admin_permission` contract (403 for provisioned-but-unassigned);
    employee actors use the plain grant check.
    """
    if user.user_type == "admin":
        await require_admin_permission(user, db, *required_permissions)
        return
    await require_permission_for_user(user, db, *required_permissions)


async def require_staff_permission_any(user: UserModel, db: AsyncSession, *codes: str) -> None:
    """Pass when the actor holds AT LEAST ONE of `codes` (delegated-People
    surfaces + the consolidated Admin roles need the OR form; every
    individual check is still the shared `require_staff_permission` engine —
    no second resolver). Denial lands in the diary through the engine."""
    last_error: Optional[ForbiddenException] = None
    for code in codes:
        try:
            await require_staff_permission(user, db, code)
            return
        except ForbiddenException as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


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
      • An admin with NO role rows is denied — UNLESS the RBAC directory
        itself has never been provisioned (the roles table is empty), the
        documented compatibility path for databases where `register_admin`
        can only attach a role when a `roles` row exists. This keeps the
        provisioning path open (an empty roles table means the portal has
        no way to assign roles yet) while closing the hole: once ANY roles
        exist, an admin without an assignment gets no unrestricted access —
        every permission-gated call returns 403 until roles are attached.
        The fallback only ever applies to `user_type == "admin"`, never to
        customer/employee surfaces.

    This reuses the existing Phase-1 RBAC helpers (`users`/`roles`/
    `permissions` join models + the built-in role vocabulary fallback) and the
    canonical capability expansion in `app.core.rbac`. It is NOT a second RBAC
    system.
    """
    roles, permissions = await get_user_roles_and_permissions(user, db)
    permission_set = set(permissions)
    # Top-level override: SUPER_ADMIN account level or role, or a wildcard
    # grant. Deliberately NOT "any admin" — ADMIN authority is capability-
    # based and always below SUPER_ADMIN (§8/§9).
    if (
        resolve_account_level(user, roles) == ACCOUNT_LEVEL_SUPER_ADMIN
        or "SUPER_ADMIN" in roles
        or "*" in permission_set
    ):
        return
    if (
        not roles
        and not getattr(user, "account_level", None)
        and not getattr(user, "custom_permissions", None)
    ):
        # Provisioned-but-unassigned admins are denied. Only an EMPTY roles
        # table (RBAC directory never provisioned) keeps compatibility
        # access — and only admins reach this point because every caller
        # sits behind get_current_admin. An explicit account level or custom
        # grant list counts as an assignment.
        roles_table_empty = (
            await db.execute(select(func.count()).select_from(RoleModel))
        ).scalar_one() == 0
        if not roles_table_empty:
            await _audit_permission_denial(
                user, required_permissions, "admin-permission (no roles assigned)"
            )
            raise ForbiddenException(
                "Your admin account has no roles assigned. "
                "Ask a SUPER_ADMIN to provision your role."
            )
        return
    missing = [perm for perm in required_permissions if perm not in permission_set]
    if missing:
        await _audit_permission_denial(user, missing, "admin-permission")
        raise ForbiddenException(
            f"You do not have permission to perform this action. "
            f"Missing required permission: {', '.join(missing)}"
        )
