"""
Instagram Reward Repository — Data access layer for Instagram Customer Reward Coupons.

Provides database CRUD operations and concurrency control for Instagram rewards:
  1. Create reward submission
  2. Get reward by ID (with optional FOR UPDATE row lock)
  3. Get rewards with filters and pagination
  4. Get rewards by customer ID
  5. Find reward by order/customer ID
  6. Update reward status, verifier audit data, and coupon relationship
  7. Concurrency & duplicate processing protection
"""

from datetime import datetime
from typing import List, Optional, Tuple
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commerce.instagram_reward import (
    InstagramRewardModel,
    INSTAGRAM_REWARD_STATUS_APPROVED,
    INSTAGRAM_REWARD_STATUS_PENDING,
)
from app.repositories.base import BaseRepository


class InstagramRewardRepository(BaseRepository[InstagramRewardModel]):
    """Data-access repository for Instagram reward submissions."""

    def __init__(self, session: AsyncSession):
        super().__init__(InstagramRewardModel, session)

    async def create_reward(
        self,
        customer_id: str,
        order_id: str,
        instagram_username: str,
        instagram_post_url: Optional[str] = None,
    ) -> InstagramRewardModel:
        """Create and store a new Instagram reward submission in PENDING status."""
        reward = InstagramRewardModel(
            customer_id=customer_id,
            order_id=order_id,
            instagram_username=instagram_username,
            instagram_post_url=instagram_post_url,
            status=INSTAGRAM_REWARD_STATUS_PENDING,
        )
        self.session.add(reward)
        await self.session.flush()
        return reward

    async def get_by_id(
        self,
        reward_id: str,
        for_update: bool = False,
    ) -> Optional[InstagramRewardModel]:
        """
        Retrieve a single reward submission by ID.
        
        Args:
            reward_id: Unique reward identifier.
            for_update: If True, executes SELECT ... FOR UPDATE to acquire a DB row lock.
                        Prevents concurrent staff approval race conditions.
        """
        stmt = select(InstagramRewardModel).where(InstagramRewardModel.id == reward_id)
        if for_update:
            stmt = stmt.with_for_update()
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_by_order_and_customer(
        self,
        order_id: str,
        customer_id: str,
    ) -> Optional[InstagramRewardModel]:
        """Find existing reward submission for a specific order and customer."""
        stmt = (
            select(InstagramRewardModel)
            .where(
                InstagramRewardModel.order_id == order_id,
                InstagramRewardModel.customer_id == customer_id,
            )
            .order_by(InstagramRewardModel.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_approved_reward_by_order(
        self,
        order_id: str,
    ) -> Optional[InstagramRewardModel]:
        """Check if an order already has an APPROVED reward."""
        stmt = select(InstagramRewardModel).where(
            InstagramRewardModel.order_id == order_id,
            InstagramRewardModel.status == INSTAGRAM_REWARD_STATUS_APPROVED,
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_rewards_by_customer(
        self,
        customer_id: str,
        skip: int = 0,
        limit: int = 50,
    ) -> Tuple[List[InstagramRewardModel], int]:
        """Retrieve paginated reward submissions for a specific customer."""
        count_stmt = select(func.count(InstagramRewardModel.id)).where(
            InstagramRewardModel.customer_id == customer_id
        )
        total_res = await self.session.execute(count_stmt)
        total = total_res.scalar_one_or_none() or 0

        stmt = (
            select(InstagramRewardModel)
            .where(InstagramRewardModel.customer_id == customer_id)
            .order_by(InstagramRewardModel.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        items = list(res.scalars().all())
        return items, total

    async def list_rewards(
        self,
        status: Optional[str] = None,
        customer_id: Optional[str] = None,
        order_id: Optional[str] = None,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> Tuple[List[InstagramRewardModel], int]:
        """
        Retrieve filtered, paginated reward submissions for admin/staff management.
        """
        stmt = select(InstagramRewardModel)
        count_stmt = select(func.count(InstagramRewardModel.id))

        filters = []
        if status:
            filters.append(InstagramRewardModel.status == status)
        if customer_id:
            filters.append(InstagramRewardModel.customer_id == customer_id)
        if order_id:
            filters.append(InstagramRewardModel.order_id == order_id)
        if search:
            search_pattern = f"%{search.strip()}%"
            filters.append(
                or_(
                    InstagramRewardModel.instagram_username.ilike(search_pattern),
                    InstagramRewardModel.order_id.ilike(search_pattern),
                )
            )

        if filters:
            stmt = stmt.where(*filters)
            count_stmt = count_stmt.where(*filters)

        total_res = await self.session.execute(count_stmt)
        total = total_res.scalar_one_or_none() or 0

        stmt = stmt.order_by(InstagramRewardModel.created_at.desc()).offset(skip).limit(limit)
        res = await self.session.execute(stmt)
        items = list(res.scalars().all())
        return items, total

    async def update_reward_verification(
        self,
        reward: InstagramRewardModel,
        status: str,
        verifier_id: str,
        verified_at: Optional[datetime],
        rejection_reason: Optional[str] = None,
        coupon_id: Optional[str] = None,
        coupon_code: Optional[str] = None,
    ) -> InstagramRewardModel:
        """
        Update status, staff verifier audit data, and coupon linkage for a reward.
        """
        from datetime import timezone
        reward.status = status
        reward.verified_by = verifier_id
        reward.verified_at = verified_at
        reward.rejection_reason = rejection_reason
        reward.coupon_id = coupon_id
        reward.coupon_code = coupon_code
        reward.updated_at = datetime.now(timezone.utc)

        self.session.add(reward)
        await self.session.flush()
        return reward
