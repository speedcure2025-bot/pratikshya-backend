"""
OAuth Service — Google and Facebook social login.

Flow:
  1. Frontend uses the provider SDK to obtain a token (Google ID token / Facebook access token).
  2. Frontend POSTs the token to our endpoint.
  3. This service verifies the token with the provider.
  4. Finds or creates the linked UserModel and OAuthAccountModel.
  5. Returns our own TokenResponse (JWT pair) — identical to password login.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import httpx
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import (
    BusinessLogicException,
    UnauthorizedException,
)
from app.models.auth.oauth_account import OAuthAccountModel
from app.models.auth.user import UserModel
from app.models.customer.customer import CustomerProfileModel
from app.models.rbac.role import RoleModel
from app.models.rbac.user_role import UserRoleModel
from app.schemas.auth.oauth import FacebookOAuthRequest, GoogleOAuthRequest
from app.schemas.auth.token import TokenResponse
from app.services.auth.auth_service import AuthService


class OAuthService:
    """Handles OAuth provider token verification and user provisioning."""

    FACEBOOK_GRAPH_URL = "https://graph.facebook.com/me"
    FACEBOOK_DEBUG_TOKEN_URL = "https://graph.facebook.com/debug_token"

    def __init__(self, db: AsyncSession):
        self.db = db
        self._auth_service = AuthService(db)

    # ------------------------------------------------------------------
    # Google
    # ------------------------------------------------------------------

    async def verify_google_token(self, token: str) -> dict:
        """Verify a Google ID token and return user info payload.

        Returns dict with keys: sub, email, name, picture, email_verified.
        Raises UnauthorizedException on invalid/expired token.
        """
        if not settings.GOOGLE_CLIENT_ID:
            raise BusinessLogicException(
                "Google OAuth is not configured. Set GOOGLE_CLIENT_ID in your environment."
            )

        try:
            request_obj = google_requests.Request()
            payload = google_id_token.verify_oauth2_token(
                token,
                request_obj,
                settings.GOOGLE_CLIENT_ID,
            )
            return payload
        except ValueError as exc:
            raise UnauthorizedException(f"Invalid Google ID token: {exc}") from exc

    async def google_login(
        self,
        req: GoogleOAuthRequest,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> TokenResponse:
        """Verify Google ID token, find-or-create user, return JWT pair."""
        payload = await self.verify_google_token(req.id_token)

        provider_user_id: str = payload["sub"]
        email: str = payload.get("email", "")
        full_name: str = payload.get("name", email.split("@")[0])
        email_verified: bool = payload.get("email_verified", False)

        if not email:
            raise UnauthorizedException(
                "Google account does not expose an email address. "
                "Please grant email permission and try again."
            )

        user = await self._find_or_create_oauth_user(
            provider="google",
            provider_user_id=provider_user_id,
            email=email,
            full_name=full_name,
            is_verified=email_verified,
            access_token=req.id_token,
        )

        return await self._auth_service._build_token_response(user, ip_address, user_agent)

    # ------------------------------------------------------------------
    # Facebook
    # ------------------------------------------------------------------

    async def verify_facebook_token(self, access_token: str) -> dict:
        """Verify a Facebook user access token and return user info.

        Calls the Graph API with the token and returns:
        dict with keys: id, name, email (email may be absent if not granted).
        Raises UnauthorizedException on failure.
        """
        if not settings.FACEBOOK_APP_ID or not settings.FACEBOOK_APP_SECRET:
            raise BusinessLogicException(
                "Facebook OAuth is not configured. "
                "Set FACEBOOK_APP_ID and FACEBOOK_APP_SECRET in your environment."
            )

        params = {
            "fields": "id,name,email",
            "access_token": access_token,
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            # 1. Fetch user info
            resp = await client.get(self.FACEBOOK_GRAPH_URL, params=params)
            if resp.status_code != 200:
                raise UnauthorizedException(
                    f"Facebook token verification failed: {resp.text}"
                )
            user_info = resp.json()

            if "error" in user_info:
                raise UnauthorizedException(
                    f"Facebook API error: {user_info['error'].get('message', 'Unknown error')}"
                )

            # 2. Verify the token is for our app (optional but recommended)
            app_token = f"{settings.FACEBOOK_APP_ID}|{settings.FACEBOOK_APP_SECRET}"
            debug_resp = await client.get(
                self.FACEBOOK_DEBUG_TOKEN_URL,
                params={"input_token": access_token, "access_token": app_token},
            )
            if debug_resp.status_code == 200:
                debug_data = debug_resp.json().get("data", {})
                if not debug_data.get("is_valid", False):
                    raise UnauthorizedException("Facebook access token is invalid or expired.")
                if debug_data.get("app_id") != settings.FACEBOOK_APP_ID:
                    raise UnauthorizedException(
                        "Facebook access token was not issued for this application."
                    )

        return user_info

    async def facebook_login(
        self,
        req: FacebookOAuthRequest,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> TokenResponse:
        """Verify Facebook access token, find-or-create user, return JWT pair."""
        user_info = await self.verify_facebook_token(req.access_token)

        provider_user_id: str = user_info["id"]
        email: Optional[str] = user_info.get("email")
        full_name: str = user_info.get("name", f"fb_{provider_user_id}")

        # Facebook may not return email if the user declined the permission.
        # We store NULL and allow the user to add an email later rather than
        # polluting the users table with unverified synthetic addresses.

        user = await self._find_or_create_oauth_user(
            provider="facebook",
            provider_user_id=provider_user_id,
            email=email,
            full_name=full_name,
            is_verified=True,
            access_token=req.access_token,
        )

        return await self._auth_service._build_token_response(user, ip_address, user_agent)

    # ------------------------------------------------------------------
    # Shared: Find or Create OAuth User
    # ------------------------------------------------------------------

    async def _find_or_create_oauth_user(
        self,
        provider: str,
        provider_user_id: str,
        email: Optional[str],
        full_name: str,
        is_verified: bool = True,
        access_token: Optional[str] = None,
        expires_at: Optional[datetime] = None,
    ) -> UserModel:
        """Core logic for find-or-create on an OAuth login.

        Priority:
          1. OAuthAccountModel match (provider + provider_user_id) → return linked user
          2. Existing UserModel with matching email (only when email is present) → link OAuth account, return user
          3. No match → create new UserModel (customer) + CustomerProfileModel + assign role
                        + create OAuthAccountModel
        """
        # 1. Look up existing OAuth account
        stmt = select(OAuthAccountModel).where(
            OAuthAccountModel.provider == provider,
            OAuthAccountModel.provider_user_id == provider_user_id,
        )
        result = await self.db.execute(stmt)
        oauth_account = result.scalars().first()

        if oauth_account:
            # Update access token if fresh
            if access_token:
                oauth_account.access_token = access_token
            # Load and return linked user
            user_stmt = select(UserModel).where(UserModel.id == oauth_account.user_id)
            user_res = await self.db.execute(user_stmt)
            user = user_res.scalars().first()
            if user and user.status != "ACTIVE":
                raise UnauthorizedException(
                    f"Account is {user.status.lower()}. Access denied."
                )
            await self.db.commit()
            return user

        # 2. Check if a user with this email already exists (only when email is provided)
        if email:
            user_stmt = select(UserModel).where(UserModel.email == email)
            user_res = await self.db.execute(user_stmt)
            existing_user = user_res.scalars().first()

            if existing_user:
                if existing_user.status != "ACTIVE":
                    raise UnauthorizedException(
                        f"Account is {existing_user.status.lower()}. Access denied."
                    )
                # Link the new OAuth account to the existing user
                new_oauth = OAuthAccountModel(
                    user_id=existing_user.id,
                    provider=provider,
                    provider_user_id=provider_user_id,
                    email=email,
                    access_token=access_token,
                    expires_at=expires_at,
                )
                self.db.add(new_oauth)
                await self.db.commit()
                return existing_user

        # 3. Create brand-new user
        new_user = UserModel(
            email=email,                    # May be None for Facebook users without email permission
            full_name=full_name,
            hashed_password=None,           # OAuth users have no password
            user_type="customer",
            status="ACTIVE",
            is_verified=is_verified,
            force_password_change=False,
        )
        self.db.add(new_user)
        await self.db.flush()

        # Customer profile
        self.db.add(CustomerProfileModel(user_id=new_user.id))

        # Assign CUSTOMER role if it exists
        role_stmt = select(RoleModel).where(RoleModel.name == "CUSTOMER")
        role_res = await self.db.execute(role_stmt)
        customer_role = role_res.scalars().first()
        if customer_role:
            self.db.add(UserRoleModel(user_id=new_user.id, role_id=customer_role.id))

        # OAuth account record
        new_oauth = OAuthAccountModel(
            user_id=new_user.id,
            provider=provider,
            provider_user_id=provider_user_id,
            email=email,
            access_token=access_token,
            expires_at=expires_at,
        )
        self.db.add(new_oauth)

        await self.db.commit()
        await self.db.refresh(new_user)
        return new_user
