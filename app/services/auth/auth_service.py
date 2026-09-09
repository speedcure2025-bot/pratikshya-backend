"""
AuthService — production authentication for Customer, Employee, and Admin surfaces.

Cache integration points
─────────────────────────────────────────────────────────────
  blacklist:access:{jti}       Access-token blacklist entry (TTL = remaining token lifetime)
  blacklist:refresh:{jti}      Refresh-token blacklist entry (TTL = remaining token lifetime)
  rbac:{user_id}               Cached JSON of { roles: [...], permissions: [...] } (TTL 5 min)
  otp:{purpose}:{user_id}      6-digit OTP hash with 10-minute TTL
  pwd_reset:{user_id}          Password-reset raw token with 1-hour TTL
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.core.exceptions import (
    BusinessLogicException,
    ConflictException,
    ForbiddenException,
    NotFoundException,
    UnauthorizedException,
)
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    token_remaining_seconds,
    verify_password,
)
from app.models.auth.session import UserSessionModel
from app.models.auth.user import UserModel
from app.models.customer.customer import CustomerProfileModel
from app.models.employee.employee import EmployeeProfileModel
from app.models.rbac.role import RoleModel
from app.models.rbac.role_permission import RolePermissionModel
from app.models.rbac.user_role import UserRoleModel
from app.schemas.auth.login import (
    AdminLoginRequest,
    AdminRegisterRequest,
    CustomerLoginRequest,
    CustomerRegisterRequest,
    EmployeeLoginRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
)
from app.schemas.auth.token import TokenResponse, UserDTO

logger = get_logger("app.auth.auth_service")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RBAC_CACHE_TTL = 300        # 5 minutes
_OTP_TTL = 600               # 10 minutes
_PASSWORD_RESET_TTL = 3600   # 1 hour

_PHONE_RE = re.compile(r"^(?:\+91|0)?[6-9]\d{9}$|^\+?[1-9]\d{1,14}$")


def _is_phone(value: str) -> bool:
    return bool(_PHONE_RE.match(value.strip()))


# ---------------------------------------------------------------------------
# AuthService
# ---------------------------------------------------------------------------

class AuthService:
    """Production service for role-based authentication (Customer, Employee, Admin)."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── RBAC helpers ──────────────────────────────────────────────────────────

    async def _get_user_roles_and_permissions(
        self, user_id: str
    ) -> Tuple[List[str], List[str]]:
        """
        Return (roles, permissions) for user_id.

        Results are cached in-process under ``rbac:{user_id}`` for 5 minutes.
        The cache is invalidated whenever a role is assigned or removed
        (handled in RBACService).
        """
        redis = get_redis()
        cache_key = f"rbac:{user_id}"

        cached = await redis.get(cache_key)
        if cached:
            data = json.loads(cached)
            return data["roles"], data["permissions"]

        # Cache miss — query DB
        stmt = (
            select(UserRoleModel)
            .where(UserRoleModel.user_id == user_id)
            .options(
                selectinload(UserRoleModel.role)
                .selectinload(RoleModel.permissions)
                .selectinload(RolePermissionModel.permission)
            )
        )
        result = await self.db.execute(stmt)
        user_roles = result.scalars().all()

        roles_list: List[str] = []
        perms_set: set = set()

        for ur in user_roles:
            if ur.role:
                roles_list.append(ur.role.name)
                for rp in ur.role.permissions:
                    if rp.permission:
                        perms_set.add(rp.permission.code)

        # Include built-in static permissions fallback for system roles (e.g. SUPER_ADMIN, ADMIN)
        from app.api.v1.admin import BUILT_IN_ROLES
        for rname in roles_list:
            role_key = rname.upper()
            if role_key in BUILT_IN_ROLES:
                for pcode in BUILT_IN_ROLES[role_key].get("permissions", []):
                    perms_set.add(pcode)

        # Super admin users always have wildcard and full workflow permissions
        if "SUPER_ADMIN" in roles_list or "ADMIN" in roles_list:
            perms_set.add("*")

        perms_list = list(perms_set)

        # Populate cache
        await redis.setex(
            cache_key,
            _RBAC_CACHE_TTL,
            json.dumps({"roles": roles_list, "permissions": perms_list}),
        )

        return roles_list, perms_list

    async def invalidate_rbac_cache(self, user_id: str) -> None:
        """Remove cached role/permission data for user_id.  Call after role changes."""
        await get_redis().delete(f"rbac:{user_id}")

    async def _build_user_dto(self, user: UserModel, roles: List[str], permissions: List[str]) -> UserDTO:
        """Build the frontend-facing identity DTO with existing profile fields."""
        extra: dict = {}
        if user.user_type == "employee":
            profile_res = await self.db.execute(
                select(EmployeeProfileModel).where(EmployeeProfileModel.user_id == user.id)
            )
            profile = profile_res.scalars().first()
            if profile:
                extra.update(
                    employee_code=profile.employee_code,
                    employeeCode=profile.employee_code,
                    designation=profile.designation,
                    department=profile.department,
                    department_id=profile.department_id,
                    section_id=profile.section_id,
                )
        elif user.user_type == "admin":
            # The current schema has no separate admin-profile table/code. Keep
            # admin identity explicit and stable by exposing the authoritative
            # user UUID under both legacy aliases without inventing DB fields.
            extra.update(admin_code=user.id, adminId=user.id)

        return UserDTO(
            id=user.id,
            email=user.email,
            phone=user.phone,
            full_name=user.full_name,
            user_type=user.user_type,
            status=user.status,
            is_verified=user.is_verified,
            force_password_change=user.force_password_change,
            roles=roles,
            permissions=permissions,
            **extra,
        )

    # ── Session helpers ───────────────────────────────────────────────────────

    async def _create_user_session(
        self,
        user_id: str,
        refresh_token: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> UserSessionModel:
        token_hash = hash_password(refresh_token)
        expires_at = datetime.now(timezone.utc) + timedelta(
            days=settings.REFRESH_TOKEN_EXPIRE_DAYS
        )

        session_record = UserSessionModel(
            user_id=user_id,
            refresh_token_hash=token_hash,
            ip_address=ip_address,
            user_agent=user_agent,
            expires_at=expires_at,
            is_revoked=False,
        )
        self.db.add(session_record)
        await self.db.flush()
        return session_record

    async def _blacklist_token(self, token: str, token_type: str = "access") -> None:
        """
        Add a token's JTI to the blacklist with a TTL equal to the
        remaining lifetime of the token.  The entry expires automatically so
        the blacklist never grows unboundedly.

        token_type should be "access" or "refresh".
        """
        payload = decode_token(token)
        if not payload:
            return  # already expired — nothing to blacklist

        jti = payload.get("jti")
        if not jti:
            return  # old-format token without jti — nothing to do

        ttl = token_remaining_seconds(payload)
        if ttl > 0:
            await get_redis().setex(
                f"blacklist:{token_type}:{jti}",
                ttl,
                "1",
            )

    # ── Token response builder ────────────────────────────────────────────────

    async def _build_token_response(
        self,
        user: UserModel,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        surface: str = "customer",
    ) -> TokenResponse:
        """
        Build the JWT token response.

        `surface` controls which alias key is populated in the response
        (user / employee / admin) as per API_CONTRACT.md.
        """
        roles, permissions = await self._get_user_roles_and_permissions(user.id)

        access_token = create_access_token(
            subject=user.id,
            user_type=user.user_type,
            extra_claims={
                "roles": roles,
                "force_password_change": user.force_password_change,
            },
        )

        refresh_token = create_refresh_token(
            subject=user.id,
            user_type=user.user_type,
        )

        await self._create_user_session(user.id, refresh_token, ip_address, user_agent)

        user_dto = await self._build_user_dto(user, roles, permissions)

        resp_kwargs: dict = dict(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            force_password_change=user.force_password_change,
            mustChangePassword=user.force_password_change,
        )

        if surface == "employee":
            resp_kwargs["employee"] = user_dto
        elif surface == "admin":
            resp_kwargs["admin"] = user_dto
        else:
            resp_kwargs["user"] = user_dto

        return TokenResponse(**resp_kwargs)

    # ── Customer Registration & Login ─────────────────────────────────────────

    async def register_customer(
        self,
        req: CustomerRegisterRequest,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> TokenResponse:
        stmt = select(UserModel).where(UserModel.email == req.email)
        res = await self.db.execute(stmt)
        if res.scalars().first():
            logger.warning("Customer registration conflict email=%s ip=%s", req.email, ip_address)
            raise ConflictException(
                "An account with this email already exists. Please sign in."
            )

        if req.phone:
            stmt_phone = select(UserModel).where(UserModel.phone == req.phone)
            res_phone = await self.db.execute(stmt_phone)
            if res_phone.scalars().first():
                logger.warning("Customer registration conflict phone=%s ip=%s", req.phone, ip_address)
                raise ConflictException(
                    "An account with this phone number already exists."
                )

        new_user = UserModel(
            email=req.email,
            phone=req.phone,
            full_name=req.full_name,
            hashed_password=hash_password(req.password),
            user_type="customer",
            status="ACTIVE",
            is_verified=True,
            force_password_change=False,
        )
        self.db.add(new_user)
        await self.db.flush()

        self.db.add(CustomerProfileModel(user_id=new_user.id))

        role_stmt = select(RoleModel).where(RoleModel.name == "CUSTOMER")
        role_res = await self.db.execute(role_stmt)
        customer_role = role_res.scalars().first()
        if customer_role:
            self.db.add(UserRoleModel(user_id=new_user.id, role_id=customer_role.id))

        await self.db.commit()
        await self.db.refresh(new_user)

        logger.info("Customer registered user_id=%s email=%s ip=%s", new_user.id, req.email, ip_address)
        return await self._build_token_response(
            new_user, ip_address, user_agent, surface="customer"
        )

    async def login_customer(
        self,
        req: CustomerLoginRequest,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> TokenResponse:
        identifier = req.identifier or req.email or ""

        if _is_phone(identifier):
            stmt = select(UserModel).where(UserModel.phone == identifier)
        else:
            stmt = select(UserModel).where(UserModel.email == identifier)

        res = await self.db.execute(stmt)
        user = res.scalars().first()

        if not user or user.user_type != "customer":
            logger.warning("Customer login failed — unknown identifier=%s ip=%s", identifier, ip_address)
            raise UnauthorizedException("That email or phone doesn't match our records.")

        if user.status in ("SUSPENDED", "DEACTIVATED"):
            logger.warning("Customer login blocked status=%s user_id=%s ip=%s", user.status, user.id, ip_address)
            raise ForbiddenException(f"Account is {user.status.lower()}. Access denied.")

        if not user.hashed_password:
            raise UnauthorizedException(
                "This account was created with social login. "
                "Please use the appropriate sign-in button, or set a password first."
            )

        if not verify_password(req.password, user.hashed_password):
            logger.warning("Customer login failed — wrong password user_id=%s ip=%s", user.id, ip_address)
            raise UnauthorizedException("That email or phone doesn't match our records.")

        logger.info("Customer login success user_id=%s ip=%s", user.id, ip_address)
        return await self._build_token_response(
            user, ip_address, user_agent, surface="customer"
        )

    # ── Employee Login ────────────────────────────────────────────────────────

    async def login_employee(
        self,
        req: EmployeeLoginRequest,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> TokenResponse:
        employee_id = req.employeeId or req.employee_code or ""

        if not employee_id:
            raise UnauthorizedException("Enter your employee ID and password.")

        if not re.match(r"^PF-[A-Z]+-\d{5}$", employee_id) and "@" not in employee_id:
            raise UnauthorizedException("That employee ID does not look right.")

        stmt = (
            select(UserModel)
            .outerjoin(
                EmployeeProfileModel,
                EmployeeProfileModel.user_id == UserModel.id,
            )
            .where(
                or_(
                    UserModel.email == employee_id,
                    EmployeeProfileModel.employee_code == employee_id,
                )
            )
        )
        res = await self.db.execute(stmt)
        user = res.scalars().first()

        if not user or user.user_type != "employee":
            raise UnauthorizedException("That employee ID does not match our records.")

        if user.status in ("SUSPENDED", "INACTIVE"):
            raise ForbiddenException(
                f"Employee account is {user.status.lower()}. Access denied."
            )

        if not user.hashed_password:
            raise UnauthorizedException(
                "This account has no credentials issued. Please contact your administrator."
            )

        if not verify_password(req.password, user.hashed_password):
            logger.warning("Employee login failed — wrong password user_id=%s ip=%s", user.id, ip_address)
            raise UnauthorizedException("Employee ID or password is not correct.")

        logger.info("Employee login success user_id=%s ip=%s", user.id, ip_address)
        return await self._build_token_response(
            user, ip_address, user_agent, surface="employee"
        )

    # ── Admin Registration ────────────────────────────────────────────────────

    async def register_admin(
        self,
        req: AdminRegisterRequest,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        actor: Optional[UserModel] = None,
    ) -> TokenResponse:
        from sqlalchemy import func as sa_func

        count_stmt = select(sa_func.count()).where(
            UserModel.user_type == "admin",
            UserModel.status == "ACTIVE",
        )
        count_res = await self.db.execute(count_stmt)
        admin_count = count_res.scalar_one()
        is_bootstrap = admin_count == 0

        bootstrap_secret = getattr(settings, "ADMIN_BOOTSTRAP_SECRET", None)

        if is_bootstrap:
            if bootstrap_secret and req.adminSecret != bootstrap_secret:
                raise ForbiddenException(
                    "ADMIN_BOOTSTRAP_SECRET is required to create the first admin account."
                )
        else:
            if actor is None:
                if not bootstrap_secret or req.adminSecret != bootstrap_secret:
                    raise ForbiddenException(
                        "Creating an admin account requires an existing SUPER_ADMIN session "
                        "or the ADMIN_BOOTSTRAP_SECRET key."
                    )
            else:
                actor_roles, _actor_permissions = await self._get_user_roles_and_permissions(actor.id)
                if "SUPER_ADMIN" not in actor_roles:
                    raise ForbiddenException("Creating an admin account requires a SUPER_ADMIN session.")

        email_stmt = select(UserModel).where(UserModel.email == req.email)
        email_res = await self.db.execute(email_stmt)
        if email_res.scalars().first():
            raise ConflictException("An account with this email already exists.")

        if req.phone:
            phone_stmt = select(UserModel).where(UserModel.phone == req.phone)
            phone_res = await self.db.execute(phone_stmt)
            if phone_res.scalars().first():
                raise ConflictException("An account with this phone number already exists.")

        new_admin = UserModel(
            email=req.email,
            phone=req.phone,
            full_name=req.full_name,
            hashed_password=hash_password(req.password),
            user_type="admin",
            status="ACTIVE",
            is_verified=True,
            force_password_change=False,
        )
        self.db.add(new_admin)
        await self.db.flush()

        role_stmt = select(RoleModel).where(RoleModel.name == "SUPER_ADMIN")
        role_res = await self.db.execute(role_stmt)
        super_admin_role = role_res.scalars().first()
        if super_admin_role:
            self.db.add(UserRoleModel(user_id=new_admin.id, role_id=super_admin_role.id))

        await self.db.commit()
        await self.db.refresh(new_admin)

        logger.info("Admin registered user_id=%s email=%s bootstrap=%s ip=%s", new_admin.id, req.email, is_bootstrap, ip_address)
        return await self._build_token_response(
            new_admin, ip_address, user_agent, surface="admin"
        )

    # ── Admin Login ───────────────────────────────────────────────────────────

    async def login_admin(
        self,
        req: AdminLoginRequest,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> TokenResponse:
        admin_identifier = req.adminId or str(req.email or "")

        if not admin_identifier:
            raise UnauthorizedException("Admin ID or email is required.")

        if _is_phone(admin_identifier):
            stmt = select(UserModel).where(UserModel.phone == admin_identifier)
        else:
            stmt = select(UserModel).where(UserModel.email == admin_identifier)

        res = await self.db.execute(stmt)
        user = res.scalars().first()

        if not user or user.user_type != "admin":
            raise ForbiddenException("Admin access privileges required.")

        if user.status == "SUSPENDED":
            raise ForbiddenException("Admin account is suspended. Access denied.")

        if not user.hashed_password:
            raise UnauthorizedException(
                "This admin account has no password set. Please contact the system administrator."
            )

        if not verify_password(req.password, user.hashed_password):
            logger.warning("Admin login failed — wrong password user_id=%s ip=%s", user.id, ip_address)
            raise UnauthorizedException("Invalid admin credentials.")

        logger.info("Admin login success user_id=%s ip=%s", user.id, ip_address)
        return await self._build_token_response(
            user, ip_address, user_agent, surface="admin"
        )

    # ── Token Refresh ─────────────────────────────────────────────────────────

    async def refresh_access_token(
        self, refresh_token: str, current_access_token: Optional[str] = None
    ) -> TokenResponse:
        """
        Exchange a valid refresh token for a new access token.

        Steps:
          1. Decode and verify the JWT signature/expiry.
          2. Confirm token_type == "refresh".
          3. Check the refresh token's JTI against the blacklist.
          4. Match against an active, non-expired DB session (bcrypt verify).
          5. Blacklist the OLD refresh token (rotation).
          6. Blacklist the OLD access token if supplied in the request.
          7. Revoke the old DB session and issue new tokens + new session.
        """
        payload = decode_token(refresh_token)
        if not payload or payload.get("token_type") != "refresh":
            raise UnauthorizedException("Invalid or expired refresh token.")

        # Check refresh token blacklist
        jti = payload.get("jti")
        if jti:
            redis = get_redis()
            if await redis.exists(f"blacklist:refresh:{jti}"):
                raise UnauthorizedException("Refresh token has been revoked.")

        user_id = payload.get("sub")
        stmt = select(UserModel).where(UserModel.id == user_id)
        res = await self.db.execute(stmt)
        user = res.scalars().first()

        if not user or user.status != "ACTIVE":
            raise UnauthorizedException("User account inactive or not found.")

        # Verify the refresh token against a live DB session
        sessions_stmt = select(UserSessionModel).where(
            UserSessionModel.user_id == user_id,
            UserSessionModel.is_revoked == False,  # noqa: E712
            UserSessionModel.expires_at > datetime.now(timezone.utc),
        )
        sessions_res = await self.db.execute(sessions_stmt)
        active_sessions = sessions_res.scalars().all()

        valid_session = next(
            (
                s
                for s in active_sessions
                if verify_password(refresh_token, s.refresh_token_hash)
            ),
            None,
        )
        if not valid_session:
            raise UnauthorizedException("Refresh token has been revoked or expired.")

        # Rotate: blacklist old refresh token + access token, revoke DB session
        await self._blacklist_token(refresh_token, token_type="refresh")
        if current_access_token:
            await self._blacklist_token(current_access_token, token_type="access")

        valid_session.is_revoked = True
        await self.db.flush()

        logger.info("Token refreshed for user_id=%s", user_id)
        surface = user.user_type
        return await self._build_token_response(user, surface=surface)

    # ── Logout ────────────────────────────────────────────────────────────────

    async def logout(
        self, user_id: str, access_token: Optional[str] = None
    ) -> bool:
        """
        Sign out a user.

        1. Blacklist the current access token in Redis (so it stops working
           immediately — before its natural expiry).
        2. Revoke all active DB sessions for the user.
        """
        # Blacklist the presented access token
        if access_token:
            await self._blacklist_token(access_token, token_type="access")

        # Revoke all DB sessions (refresh tokens)
        stmt = select(UserSessionModel).where(
            UserSessionModel.user_id == user_id,
            UserSessionModel.is_revoked == False,  # noqa: E712
        )
        res = await self.db.execute(stmt)
        sessions = res.scalars().all()
        for session in sessions:
            session.is_revoked = True

        await self.db.commit()
        logger.info("User logged out user_id=%s", user_id)
        return True

    # ── Change Password ───────────────────────────────────────────────────────

    async def change_password(
        self,
        user_id: str,
        old_password: str,
        new_password: str,
        access_token: Optional[str] = None,
    ) -> bool:
        """
        Change password and invalidate all active sessions.

        On success:
          - Updates the password hash.
          - Clears force_password_change flag.
          - Revokes all active DB sessions.
          - Blacklists the current access token.
          - Invalidates the RBAC cache (force re-read of force_password_change).
        """
        stmt = select(UserModel).where(UserModel.id == user_id)
        res = await self.db.execute(stmt)
        user = res.scalars().first()

        if not user:
            raise NotFoundException("User not found.")

        if not user.hashed_password:
            raise BusinessLogicException(
                "This account uses social login and has no password. "
                "Please use your social provider to authenticate."
            )

        if not verify_password(old_password, user.hashed_password):
            raise BusinessLogicException("Current password is not correct.")

        user.hashed_password = hash_password(new_password)
        user.force_password_change = False

        # Revoke all DB sessions
        sessions_stmt = select(UserSessionModel).where(
            UserSessionModel.user_id == user_id,
            UserSessionModel.is_revoked == False,  # noqa: E712
        )
        sessions_res = await self.db.execute(sessions_stmt)
        for s in sessions_res.scalars().all():
            s.is_revoked = True

        await self.db.commit()

        # Blacklist current access token + clear RBAC cache
        if access_token:
            await self._blacklist_token(access_token, token_type="access")
        await self.invalidate_rbac_cache(user_id)

        logger.info("Password changed user_id=%s", user_id)
        return True

    # ── OTP ───────────────────────────────────────────────────────────────────

    async def generate_otp(self, user_id: str, purpose: str = "verify") -> str:
        """
        Generate a 6-digit OTP, store its hash in the cache with a 10-minute TTL,
        and return the raw OTP (caller passes it to Notification_Service).

        The hash is stored so the raw OTP never persists anywhere.
        Key format: ``otp:{purpose}:{user_id}``
        """
        otp = str(secrets.randbelow(900_000) + 100_000)  # always 6 digits
        otp_hash = hash_password(otp)

        await get_redis().setex(
            f"otp:{purpose}:{user_id}",
            _OTP_TTL,
            otp_hash,
        )
        return otp

    async def verify_otp(self, user_id: str, otp: str, purpose: str = "verify") -> bool:
        """
        Verify an OTP against the cached hash.

        The key is deleted immediately on a successful match (single-use).
        Raises UnauthorizedException if the OTP is missing or incorrect.
        """
        redis = get_redis()
        key = f"otp:{purpose}:{user_id}"
        stored_hash = await redis.get(key)

        if not stored_hash:
            raise UnauthorizedException("OTP has expired or was never issued.")

        if not verify_password(otp, stored_hash):
            raise UnauthorizedException("Incorrect OTP.")

        await redis.delete(key)  # single-use — delete after successful verify
        return True

    # ── Forgot / Reset Password ───────────────────────────────────────────────

    async def forgot_password(self, req: ForgotPasswordRequest) -> str:
        """
        Generate a time-limited password-reset token and store it in the cache.

        The raw token is returned so the caller can pass it to
        Notification_Service for email/SMS dispatch.  A success-like message
        is always returned regardless of whether the account exists, to
        prevent account enumeration.
        """
        identifier = req.identifier.strip()

        if _is_phone(identifier):
            stmt = select(UserModel).where(UserModel.phone == identifier)
        else:
            stmt = select(UserModel).where(UserModel.email == identifier)

        res = await self.db.execute(stmt)
        user = res.scalars().first()

        message = (
            f"Password reset instructions have been sent to {identifier}."
        )

        if user:
            raw_token = secrets.token_urlsafe(32)
            # Store the raw token in Redis with a 1-hour TTL.
            # The token is looked up directly (no bcrypt round-trip needed for
            # short-lived tokens — secrets.token_urlsafe(32) is already
            # cryptographically random and resistant to enumeration).
            await get_redis().setex(
                f"pwd_reset:{user.id}",
                _PASSWORD_RESET_TTL,
                raw_token,
            )
            # TODO: pass raw_token to Notification_Service for email dispatch
            # e.g. await notification_service.send_password_reset_email(user, raw_token)

        return message

    async def reset_password(self, token: str, new_password: str) -> bool:
        """
        Verify a password-reset token and update the password.

        Lookup flow:
          1. Iterate active users and check if ``pwd_reset:{user_id}`` matches
             the supplied token.  (In practice, embed user_id in the reset URL.)
          2. Delete the Redis key (single-use).
          3. Hash and store the new password.
          4. Revoke all active DB sessions.
          5. Invalidate RBAC cache.

        NOTE: The reset URL sent by email should include the user_id so this
        endpoint can do a direct O(1) Redis lookup rather than scanning all keys.
        For now the token is accepted with the user_id provided separately.
        """
        if not token or len(new_password) < 8:
            raise BusinessLogicException(
                "Password must be at least 8 characters."
            )

        # TODO: The endpoint should receive user_id alongside the token so we
        # can do a direct Redis lookup:
        #   stored = await redis.get(f"pwd_reset:{user_id}")
        #   if stored != token: raise ...
        # Until the endpoint schema is updated, raise a clear placeholder.
        raise BusinessLogicException(
            "Password reset requires the user_id embedded in the reset URL. "
            "Update the reset-password endpoint to include it."
        )

    async def reset_password_with_user_id(
        self, user_id: str, token: str, new_password: str
    ) -> bool:
        """
        Full implementation — called when the reset URL includes user_id.
        """
        if len(new_password) < 8:
            raise BusinessLogicException("Password must be at least 8 characters.")

        redis = get_redis()
        key = f"pwd_reset:{user_id}"
        stored_token = await redis.get(key)

        if not stored_token or stored_token != token:
            raise UnauthorizedException(
                "Password reset token is invalid or has expired."
            )

        # Token is valid — delete it (single-use)
        await redis.delete(key)

        stmt = select(UserModel).where(UserModel.id == user_id)
        res = await self.db.execute(stmt)
        user = res.scalars().first()
        if not user:
            raise NotFoundException("User not found.")

        user.hashed_password = hash_password(new_password)
        user.force_password_change = False

        # Revoke all active sessions
        sessions_stmt = select(UserSessionModel).where(
            UserSessionModel.user_id == user_id,
            UserSessionModel.is_revoked == False,  # noqa: E712
        )
        sessions_res = await self.db.execute(sessions_stmt)
        for s in sessions_res.scalars().all():
            s.is_revoked = True

        await self.db.commit()
        await self.invalidate_rbac_cache(user_id)

        return True
