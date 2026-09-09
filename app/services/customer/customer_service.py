from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.constants import REVENUE_ORDER_STATUSES
from app.core.exceptions import ConflictException, NotFoundException
from app.models.auth.session import UserSessionModel
from app.models.auth.user import UserModel
from app.models.customer.customer import CustomerProfileModel
from app.models.customer.preferences import CustomerPreferencesModel
from app.models.orders.order import OrderModel
from app.schemas.customer.customer import (
    AdminCustomerResponse,
    PreferencesUpdate,
    ProfileUpdate,
    SessionSummary,
)


class CustomerService:
    """Business logic for customer profile management."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _order_aggregates_for(self, customer_ids: list) -> dict:
        """
        Grouped order aggregates for the given customer ids.

        Semantics (mirrors the admin analytics overview):
          - `order_count`    — every order the customer has placed, regardless
                               of status (an honest count of placed orders).
          - `lifetime_spend` — sum of `OrderModel.total` over
                               REVENUE_ORDER_STATUSES only, so cancelled /
                               refunded-wholesale orders do not inflate spend.

        Returns {customer_id: (order_count, lifetime_spend)}; customers with
        no orders are simply absent from the map.
        """
        if not customer_ids:
            return {}
        revenue_total = func.coalesce(
            func.sum(
                case(
                    (OrderModel.status.in_(REVENUE_ORDER_STATUSES), OrderModel.total),
                    else_=0,
                )
            ),
            0,
        )
        res = await self.db.execute(
            select(OrderModel.customer_id, func.count(), revenue_total)
            .where(OrderModel.customer_id.in_(customer_ids))
            .group_by(OrderModel.customer_id)
        )
        return {row[0]: (int(row[1]), float(row[2] or 0)) for row in res.all()}

    async def _get_profile_for_user(self, user_id: str) -> Tuple[UserModel, CustomerProfileModel]:
        """Load UserModel + CustomerProfileModel together, raise 404 if not found."""
        stmt = (
            select(UserModel)
            .where(UserModel.id == user_id, UserModel.user_type == "customer")
            .options(
                selectinload(UserModel.customer_profile)
                .selectinload(CustomerProfileModel.addresses),
                selectinload(UserModel.customer_profile)
                .selectinload(CustomerProfileModel.preferences),
            )
        )
        res = await self.db.execute(stmt)
        user = res.scalars().first()
        if not user or not user.customer_profile:
            raise NotFoundException("Customer profile not found.")
        return user, user.customer_profile

    async def _get_or_create_preferences(
        self, profile: CustomerProfileModel
    ) -> CustomerPreferencesModel:
        if profile.preferences:
            return profile.preferences
        prefs = CustomerPreferencesModel(customer_id=profile.id)
        self.db.add(prefs)
        await self.db.flush()
        await self.db.refresh(prefs)
        return prefs

    # ------------------------------------------------------------------
    # GET /customers/me
    # ------------------------------------------------------------------

    async def get_me(self, user_id: str, current_session_id: Optional[str] = None):
        """
        Return the full customer profile dict:
          { profile, addresses, preferences, security: { activeSessions } }

        BACKEND_GAP: `current_session_id` is never supplied by the router
        because the access token carries no session id claim — every
        SessionSummary therefore reports is_current=False.
        """
        user, profile = await self._get_profile_for_user(user_id)
        prefs = await self._get_or_create_preferences(profile)

        # Active sessions
        sessions_stmt = select(UserSessionModel).where(
            UserSessionModel.user_id == user_id,
            UserSessionModel.is_revoked == False,
            UserSessionModel.expires_at > datetime.now(timezone.utc),
        )
        sess_res = await self.db.execute(sessions_stmt)
        raw_sessions = sess_res.scalars().all()

        active_sessions = [
            SessionSummary(
                id=s.id,
                ip_address=s.ip_address,
                user_agent=s.user_agent,
                created_at=s.created_at,
                expires_at=s.expires_at,
                is_current=(s.id == current_session_id),
            )
            for s in raw_sessions
        ]

        return user, profile, prefs, active_sessions

    # ------------------------------------------------------------------
    # PATCH /customers/me
    # ------------------------------------------------------------------

    async def update_profile(self, user_id: str, data: ProfileUpdate) -> Tuple[UserModel, CustomerProfileModel]:
        user, profile = await self._get_profile_for_user(user_id)

        # Fields that live on UserModel
        if data.email is not None:
            email_str = str(data.email)
            # Uniqueness check
            dup = await self.db.execute(
                select(UserModel).where(
                    UserModel.email == email_str,
                    UserModel.id != user_id,
                )
            )
            if dup.scalars().first():
                raise ConflictException("That email address is already in use.")
            user.email = email_str

        if data.phone is not None:
            dup = await self.db.execute(
                select(UserModel).where(
                    UserModel.phone == data.phone,
                    UserModel.id != user_id,
                )
            )
            if dup.scalars().first():
                raise ConflictException("That phone number is already in use.")
            user.phone = data.phone
            # Also keep full_name in sync if first/last provided
        
        # Fields that live on CustomerProfileModel
        if data.first_name is not None:
            profile.first_name = data.first_name
        if data.last_name is not None:
            profile.last_name = data.last_name
        if data.date_of_birth is not None:
            profile.date_of_birth = data.date_of_birth
        if data.avatar is not None:
            profile.avatar = data.avatar

        # Keep full_name on UserModel in sync
        first = profile.first_name or ""
        last = profile.last_name or ""
        if first or last:
            user.full_name = f"{first} {last}".strip()

        await self.db.commit()
        await self.db.refresh(user)
        await self.db.refresh(profile)
        return user, profile

    # ------------------------------------------------------------------
    # PATCH /customers/me/preferences
    # ------------------------------------------------------------------

    async def update_preferences(
        self, user_id: str, data: PreferencesUpdate
    ) -> CustomerPreferencesModel:
        _, profile = await self._get_profile_for_user(user_id)
        prefs = await self._get_or_create_preferences(profile)

        if data.email_notifications is not None:
            prefs.email_notifications = data.email_notifications
        if data.sms_notifications is not None:
            prefs.sms_notifications = data.sms_notifications
        if data.promotional_updates is not None:
            prefs.promotional_updates = data.promotional_updates
        if data.order_updates is not None:
            prefs.order_updates = data.order_updates
        if data.styling_invitations is not None:
            prefs.styling_invitations = data.styling_invitations

        await self.db.commit()
        await self.db.refresh(prefs)
        return prefs

    # ------------------------------------------------------------------
    # POST /customers/me/sessions/revoke-others
    # ------------------------------------------------------------------

    async def revoke_other_sessions(self, user_id: str, current_token_hash_prefix: Optional[str] = None) -> int:
        """
        Revoke all sessions except the current one. Returns count revoked.

        BACKEND_GAP: `current_token_hash_prefix` is never supplied by the
        router (the access token carries no session id claim), so this
        revokes EVERY active session, including the caller's own.
        """
        stmt = select(UserSessionModel).where(
            UserSessionModel.user_id == user_id,
            UserSessionModel.is_revoked == False,
            UserSessionModel.expires_at > datetime.now(timezone.utc),
        )
        res = await self.db.execute(stmt)
        sessions = res.scalars().all()

        revoked = 0
        for s in sessions:
            # Skip current session if we can identify it
            if current_token_hash_prefix and s.id == current_token_hash_prefix:
                continue
            s.is_revoked = True
            revoked += 1

        await self.db.commit()
        return revoked

    # ------------------------------------------------------------------
    # Admin: GET /admin/customers
    # ------------------------------------------------------------------

    async def list_customers(
        self,
        q: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[dict], int]:
        """
        Return paginated customer list with derived stats.
        Stats (orderCount, lifetimeSpend) are stubbed — wire in real order aggregation
        once the Orders table has real data.
        """
        base_stmt = (
            select(UserModel)
            .where(UserModel.user_type == "customer")
            .options(
                selectinload(UserModel.customer_profile)
                .selectinload(CustomerProfileModel.addresses),
                selectinload(UserModel.customer_profile)
                .selectinload(CustomerProfileModel.preferences),
            )
        )

        if q:
            like = f"%{q}%"
            base_stmt = base_stmt.where(
                UserModel.full_name.ilike(like)
                | UserModel.email.ilike(like)
                | UserModel.phone.ilike(like)
            )

        # Total count
        count_stmt = select(func.count()).select_from(
            select(UserModel)
            .where(UserModel.user_type == "customer")
            .subquery()
        )
        if q:
            like = f"%{q}%"
            count_stmt = select(func.count()).select_from(
                select(UserModel)
                .where(
                    UserModel.user_type == "customer",
                    UserModel.full_name.ilike(like)
                    | UserModel.email.ilike(like)
                    | UserModel.phone.ilike(like),
                )
                .subquery()
            )

        count_res = await self.db.execute(count_stmt)
        total = count_res.scalar_one()

        # Paginate
        offset = (page - 1) * page_size
        base_stmt = base_stmt.order_by(UserModel.created_at.desc()).offset(offset).limit(page_size)

        users_res = await self.db.execute(base_stmt)
        users = users_res.scalars().all()

        # Real order aggregates (single grouped query for the whole page).
        order_stats = await self._order_aggregates_for([u.id for u in users])

        result = []
        for u in users:
            cp = u.customer_profile
            order_count, lifetime_spend = order_stats.get(u.id, (0, 0.0))
            result.append(
                AdminCustomerResponse(
                    id=u.id,
                    first_name=cp.first_name if cp else None,
                    last_name=cp.last_name if cp else None,
                    email=u.email,
                    phone=u.phone,
                    status=u.status,
                    loyalty_tier=cp.loyalty_tier if cp else "BRONZE",
                    loyalty_points=cp.loyalty_points if cp else 0,
                    created_at=u.created_at,
                    order_count=order_count,
                    lifetime_spend=lifetime_spend,
                    addresses=[],        # stripped for list view to keep payload small
                )
            )
        return result, total

    # ------------------------------------------------------------------
    # Admin: GET /admin/customers/{customerId}
    # ------------------------------------------------------------------

    async def get_customer_detail(self, customer_id: str) -> AdminCustomerResponse:
        stmt = (
            select(UserModel)
            .where(UserModel.id == customer_id, UserModel.user_type == "customer")
            .options(
                selectinload(UserModel.customer_profile)
                .selectinload(CustomerProfileModel.addresses),
            )
        )
        res = await self.db.execute(stmt)
        user = res.scalars().first()
        if not user:
            raise NotFoundException("Customer not found.")

        cp = user.customer_profile
        from app.schemas.customer.address import AddressResponse
        addresses = [
            AddressResponse.model_validate(addr)
            for addr in (cp.addresses if cp else [])
        ]
        order_count, lifetime_spend = (await self._order_aggregates_for([user.id])).get(
            user.id, (0, 0.0)
        )
        return AdminCustomerResponse(
            id=user.id,
            first_name=cp.first_name if cp else None,
            last_name=cp.last_name if cp else None,
            email=user.email,
            phone=user.phone,
            status=user.status,
            loyalty_tier=cp.loyalty_tier if cp else "BRONZE",
            loyalty_points=cp.loyalty_points if cp else 0,
            created_at=user.created_at,
            order_count=order_count,
            lifetime_spend=lifetime_spend,
            addresses=addresses,
        )
