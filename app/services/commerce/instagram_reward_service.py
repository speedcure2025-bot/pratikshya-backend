"""
Instagram Reward Service — Business logic layer for Instagram Customer Reward Coupons.

Handles:
  1. Customer reward submission & order eligibility verification
  2. Concurrency-safe staff approval, automated coupon generation, & atomic commitment
  3. Staff rejection flow with rejection reasoning audit
  4. Querying & pagination for customer history and admin management
"""

import secrets
import string
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import (
    BusinessLogicException,
    ConflictException,
    ForbiddenException,
    NotFoundException,
)
from app.core.logging import get_logger
from app.models.auth.user import UserModel
from app.models.commerce.coupon import CouponModel
from app.models.commerce.instagram_reward import (
    InstagramRewardModel,
    INSTAGRAM_REWARD_STATUS_APPROVED,
    INSTAGRAM_REWARD_STATUS_PENDING,
    INSTAGRAM_REWARD_STATUS_REJECTED,
)
from app.models.orders.order import OrderModel
from app.repositories.commerce.instagram_reward_repository import InstagramRewardRepository
from app.schemas.commerce.instagram_reward import (
    InstagramRewardApproveRequest,
    InstagramRewardCouponResponse,
    InstagramRewardDetailResponse,
    InstagramRewardListResponse,
    InstagramRewardManualClaimRequest,
    InstagramRewardRejectRequest,
    InstagramRewardSubmitRequest,
)

logger = get_logger("app.services.commerce.instagram_reward")


class InstagramRewardService:
    """Business logic service for Instagram customer rewards and coupons."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.reward_repo = InstagramRewardRepository(session)

    # ── Helper: Unique Coupon Code Generation ─────────────────────────────────

    async def _generate_unique_coupon_code(self, length: int = 8) -> str:
        """Generates a unique, collision-free reward coupon code."""
        alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # ambiguous chars omitted
        for _ in range(10):
            suffix = "".join(secrets.choice(alphabet) for _ in range(length))
            candidate_code = f"INSTA-{suffix}"
            stmt = select(CouponModel.id).where(CouponModel.code == candidate_code)
            res = await self.session.execute(stmt)
            if res.scalar_one_or_none() is None:
                return candidate_code
        # Fallback with timestamp microsecond suffix if 10 collisions occur
        micro = datetime.now(timezone.utc).strftime("%f")[:4]
        return f"INSTA-REW-{micro}"

    # ── Helper: Rich Object Formatting ────────────────────────────────────────

    async def _to_detail_response(
        self, reward: InstagramRewardModel
    ) -> InstagramRewardDetailResponse:
        """Enriches DB model with customer, order, verifier, and coupon details."""
        customer_name, customer_email = None, None
        if reward.customer_id:
            cust_stmt = select(UserModel).where(UserModel.id == reward.customer_id)
            cust_res = await self.session.execute(cust_stmt)
            cust = cust_res.scalars().first()
            if cust:
                customer_name = cust.full_name
                customer_email = cust.email

        order_number = None
        if reward.order_id:
            ord_stmt = select(OrderModel.order_number).where(OrderModel.id == reward.order_id)
            ord_res = await self.session.execute(ord_stmt)
            order_number = ord_res.scalar_one_or_none()

        verifier_name = None
        if reward.verified_by:
            ver_stmt = select(UserModel.full_name).where(UserModel.id == reward.verified_by)
            ver_res = await self.session.execute(ver_stmt)
            verifier_name = ver_res.scalar_one_or_none()

        coupon_resp: Optional[InstagramRewardCouponResponse] = None
        if reward.coupon_id:
            coup_stmt = select(CouponModel).where(CouponModel.id == reward.coupon_id)
            coup_res = await self.session.execute(coup_stmt)
            coup = coup_res.scalars().first()
            if coup:
                coupon_resp = InstagramRewardCouponResponse(
                    id=coup.id,
                    code=coup.code,
                    discount_percentage=float(coup.discount_value),
                    customer_id=reward.customer_id,
                    is_used=(coup.usage_count >= (coup.usage_limit or 1)),
                    usage_limit=coup.usage_limit or 1,
                    times_used=coup.usage_count,
                    valid_until=coup.expires_at,
                    created_at=coup.created_at,
                )

        return InstagramRewardDetailResponse(
            id=reward.id,
            customer_id=reward.customer_id,
            customer_name=customer_name,
            customer_email=customer_email,
            order_id=reward.order_id,
            order_number=order_number,
            instagram_username=reward.instagram_username,
            instagram_post_url=reward.instagram_post_url,
            status=reward.status,
            verified_by=reward.verified_by,
            verifier_name=verifier_name,
            verified_at=reward.verified_at,
            rejection_reason=reward.rejection_reason,
            coupon_id=reward.coupon_id,
            coupon_code=reward.coupon_code,
            coupon=coupon_resp,
            created_at=reward.created_at,
            updated_at=reward.updated_at,
        )

    # ── 1. Customer Reward Submission Flow ───────────────────────────────────

    async def submit_reward(
        self,
        customer: UserModel,
        payload: InstagramRewardSubmitRequest,
    ) -> InstagramRewardDetailResponse:
        """
        Processes a customer submission for an Instagram purchase reward.
        
        Validates order ownership, purchase completion, and duplicate submission rules.
        """
        # 1. Fetch & validate qualifying order
        order_stmt = select(OrderModel).where(OrderModel.id == payload.order_id)
        order_res = await self.session.execute(order_stmt)
        order = order_res.scalars().first()

        if not order:
            logger.warning(
                "Invalid order submitted for Instagram reward",
                extra={"order_id": payload.order_id, "customer_id": customer.id},
            )
            raise NotFoundException(f"Order '{payload.order_id}' was not found")

        # 2. Confirm order belongs to authenticated customer
        if order.customer_id != customer.id:
            logger.warning(
                "Unauthorized attempt: customer tried to claim reward for order belonging to another user",
                extra={"customer_id": customer.id, "order_id": payload.order_id},
            )
            raise ForbiddenException("This order does not belong to your account")

        # 3. Confirm order status (PAID or COMPLETED/DELIVERED/CONFIRMED)
        valid_payment_statuses = {"PAID"}
        valid_order_statuses = {
            "DELIVERED", "COMPLETED", "ORDER_CONFIRMED", "SHIPPED",
            "DISPATCHED", "OUT_FOR_DELIVERY"
        }
        if order.payment_status not in valid_payment_statuses and order.status not in valid_order_statuses:
            logger.warning(
                "Invalid order state for Instagram reward",
                extra={
                    "order_id": payload.order_id,
                    "order_status": order.status,
                    "payment_status": order.payment_status,
                },
            )
            raise BusinessLogicException(
                "Order must be paid or completed to be eligible for an Instagram reward"
            )

        # 4. Check for duplicate reward submissions
        existing = await self.reward_repo.get_by_order_and_customer(
            order_id=payload.order_id,
            customer_id=customer.id,
        )
        if existing:
            if existing.status == INSTAGRAM_REWARD_STATUS_APPROVED:
                logger.warning(
                    "Duplicate reward submission attempt for already approved order",
                    extra={"order_id": payload.order_id, "customer_id": customer.id},
                )
                raise ConflictException("An approved Instagram reward already exists for this order")
            elif existing.status == INSTAGRAM_REWARD_STATUS_PENDING:
                logger.warning(
                    "Duplicate reward submission attempt for pending order",
                    extra={"order_id": payload.order_id, "customer_id": customer.id},
                )
                raise ConflictException(
                    "An Instagram reward submission for this order is currently pending verification"
                )

        # 5. Create reward submission in PENDING status
        reward = await self.reward_repo.create_reward(
            customer_id=customer.id,
            order_id=payload.order_id,
            instagram_username=payload.instagram_username,
            instagram_post_url=payload.instagram_post_url,
        )
        await self.session.commit()

        logger.info(
            "Reward submitted successfully",
            extra={
                "reward_id": reward.id,
                "customer_id": customer.id,
                "order_id": payload.order_id,
                "instagram_username": payload.instagram_username,
            },
        )

        return await self._to_detail_response(reward)

    # ── 2. Admin Approval Flow ────────────────────────────────────────────────

    async def approve_reward(
        self,
        reward_id: str,
        verifier: UserModel,
        payload: Optional[InstagramRewardApproveRequest] = None,
    ) -> InstagramRewardDetailResponse:
        """
        Approves a PENDING Instagram reward claim and generates a personal discount coupon.
        
        Enforces SELECT FOR UPDATE row locking and atomic commitment.
        """
        # 1. Acquire pessimistic row lock to prevent race conditions
        reward = await self.reward_repo.get_by_id(reward_id, for_update=True)
        if not reward:
            raise NotFoundException(f"Instagram reward '{reward_id}' was not found")

        # 2. Check current status
        if reward.status != INSTAGRAM_REWARD_STATUS_PENDING:
            logger.warning(
                "Duplicate approval attempt on non-pending reward",
                extra={
                    "reward_id": reward_id,
                    "status": reward.status,
                    "verifier_id": verifier.id,
                },
            )
            raise ConflictException(
                f"Cannot approve reward in status '{reward.status}'. It must be PENDING."
            )

        # 3. Double-check order approval uniqueness
        existing_approved = await self.reward_repo.get_approved_reward_by_order(reward.order_id)
        if existing_approved and existing_approved.id != reward.id:
            logger.warning(
                "Duplicate approval attempt for order with existing approved reward",
                extra={"reward_id": reward_id, "order_id": reward.order_id},
            )
            raise ConflictException("An approved reward already exists for this order")

        # 4. Fetch qualifying order for coupon metadata
        order_stmt = select(OrderModel).where(OrderModel.id == reward.order_id)
        order_res = await self.session.execute(order_stmt)
        order = order_res.scalars().first()

        order_num = order.order_number if order else reward.order_id
        discount_pct = float(
            payload.discount_percentage
            if payload and payload.discount_percentage is not None
            else settings.INSTAGRAM_REWARD_DISCOUNT_PERCENT
        )        # 5. Generate unique coupon code
        coupon_code = await self._generate_unique_coupon_code()

        # 6. Instantiate personal coupon bound to this customer
        coupon = CouponModel(
            code=coupon_code,
            name=f"Instagram Reward - Order #{order_num}",
            description=f"{discount_pct}% reward coupon for tagging Pratikshya Fashion on Instagram.",
            discount_type="percentage",
            discount_value=discount_pct,
            minimum_order_value=0,
            usage_limit=1,
            usage_count=0,
            per_customer_limit=1,
            eligible_customer_ids=[reward.customer_id],  # Restricted exclusively to this customer
            is_stackable=False,
            is_active=True,
            created_by=verifier.id,
        )
        self.session.add(coupon)
        await self.session.flush()

        logger.info(
            "Coupon generated",
            extra={
                "coupon_code": coupon.code,
                "coupon_id": coupon.id,
                "discount_percentage": discount_pct,
                "customer_id": reward.customer_id,
            },
        )

        # 7. Update reward state & audit fields
        now = datetime.now(timezone.utc)
        await self.reward_repo.update_reward_verification(
            reward=reward,
            status=INSTAGRAM_REWARD_STATUS_APPROVED,
            verifier_id=verifier.id,
            verified_at=now,
            coupon_id=coupon.id,
            coupon_code=coupon.code,
        )

        # 8. Commit transaction atomically
        await self.session.commit()

        logger.info(
            "Reward approved",
            extra={
                "reward_id": reward_id,
                "verifier_id": verifier.id,
                "customer_id": reward.customer_id,
                "order_id": reward.order_id,
                "coupon_code": coupon.code,
            },
        )

        return await self._to_detail_response(reward)

    # ── 3. Admin Rejection Flow ───────────────────────────────────────────────

    async def reject_reward(
        self,
        reward_id: str,
        verifier: UserModel,
        payload: InstagramRewardRejectRequest,
    ) -> InstagramRewardDetailResponse:
        """Rejects a PENDING Instagram reward claim with staff feedback."""
        # 1. Lock & fetch reward
        reward = await self.reward_repo.get_by_id(reward_id, for_update=True)
        if not reward:
            raise NotFoundException(f"Instagram reward '{reward_id}' was not found")

        # 2. Check current status
        if reward.status != INSTAGRAM_REWARD_STATUS_PENDING:
            logger.warning(
                "Attempted rejection on non-pending reward",
                extra={
                    "reward_id": reward_id,
                    "status": reward.status,
                    "verifier_id": verifier.id,
                },
            )
            raise ConflictException(
                f"Cannot reject reward in status '{reward.status}'. It must be PENDING."
            )

        # 3. Update status & audit trail
        now = datetime.now(timezone.utc)
        await self.reward_repo.update_reward_verification(
            reward=reward,
            status=INSTAGRAM_REWARD_STATUS_REJECTED,
            verifier_id=verifier.id,
            verified_at=now,
            rejection_reason=payload.reason,
        )

        # 4. Commit transaction
        await self.session.commit()

        logger.info(
            "Reward rejected",
            extra={
                "reward_id": reward_id,
                "verifier_id": verifier.id,
                "reason": payload.reason,
            },
        )

        return await self._to_detail_response(reward)

    # ── 4. Admin Re-open Flow ─────────────────────────────────────────────────

    async def reopen_reward(
        self,
        reward_id: str,
        verifier: UserModel,
    ) -> InstagramRewardDetailResponse:
        """Re-opens a REJECTED reward back to PENDING so it can be re-reviewed."""
        reward = await self.reward_repo.get_by_id(reward_id, for_update=True)
        if not reward:
            raise NotFoundException(f"Instagram reward '{reward_id}' was not found")

        if reward.status != INSTAGRAM_REWARD_STATUS_REJECTED:
            raise ConflictException(
                f"Only REJECTED rewards can be re-opened. Current status: '{reward.status}'."
            )

        await self.reward_repo.update_reward_verification(
            reward=reward,
            status=INSTAGRAM_REWARD_STATUS_PENDING,
            verifier_id=verifier.id,
            verified_at=None,
            rejection_reason=None,
        )
        await self.session.commit()

        logger.info(
            "Reward re-opened to PENDING",
            extra={"reward_id": reward_id, "verifier_id": verifier.id},
        )

        return await self._to_detail_response(reward)

    # ── 5. Queries & Admin Management ─────────────────────────────────────────

    async def get_reward_by_id(self, reward_id: str) -> InstagramRewardDetailResponse:
        """Retrieves a single reward detail by ID."""
        reward = await self.reward_repo.get_by_id(reward_id)
        if not reward:
            raise NotFoundException(f"Instagram reward '{reward_id}' was not found")
        return await self._to_detail_response(reward)

    async def list_rewards(
        self,
        status: Optional[str] = None,
        customer_id: Optional[str] = None,
        order_id: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
    ) -> InstagramRewardListResponse:
        """Retrieves paginated rewards for admin management."""
        skip = (page - 1) * limit
        items, total = await self.reward_repo.list_rewards(
            status=status,
            customer_id=customer_id,
            order_id=order_id,
            search=search,
            skip=skip,
            limit=limit,
        )

        detail_items = [await self._to_detail_response(item) for item in items]
        total_pages = (total + limit - 1) // limit if limit > 0 else 0

        return InstagramRewardListResponse(
            items=detail_items,
            total=total,
            page=page,
            limit=limit,
            total_pages=total_pages,
        )

    async def get_customer_rewards(
        self,
        customer_id: str,
        page: int = 1,
        limit: int = 20,
    ) -> InstagramRewardListResponse:
        """Retrieves reward history for an authenticated customer."""
        skip = (page - 1) * limit
        items, total = await self.reward_repo.get_rewards_by_customer(
            customer_id=customer_id,
            skip=skip,
            limit=limit,
        )

        detail_items = [await self._to_detail_response(item) for item in items]
        total_pages = (total + limit - 1) // limit if limit > 0 else 0

        return InstagramRewardListResponse(
            items=detail_items,
            total=total,
            page=page,
            limit=limit,
            total_pages=total_pages,
        )

    # ── 5. Staff Manual Entry ─────────────────────────────────────────────────

    async def create_manual_claim(
        self,
        staff_user: UserModel,
        payload: InstagramRewardManualClaimRequest,
    ) -> InstagramRewardDetailResponse:
        """
        Manually creates and optionally approves an Instagram reward claim on behalf of a customer.
        """
        # 1. Resolve customer by ID or Email
        cust_ident = payload.customer_identifier.strip()
        cust_stmt = select(UserModel).where(
            (UserModel.id == cust_ident) | (UserModel.email == cust_ident)
        )
        cust_res = await self.session.execute(cust_stmt)
        customer = cust_res.scalars().first()
        if not customer:
            raise NotFoundException(f"Customer '{cust_ident}' was not found")

        # 2. Resolve order by ID or Order Number
        ord_ident = payload.order_identifier.strip()
        ord_stmt = select(OrderModel).where(
            (OrderModel.id == ord_ident) | (OrderModel.order_number == ord_ident)
        )
        ord_res = await self.session.execute(ord_stmt)
        order = ord_res.scalars().first()
        if not order:
            raise NotFoundException(f"Order '{ord_ident}' was not found")

        if order.customer_id != customer.id:
            raise ForbiddenException(
                f"Order '{order.order_number or order.id}' does not belong to customer '{customer.email or customer.id}'"
            )

        # 3. Check for existing approved reward for this order
        existing = await self.reward_repo.get_by_order_and_customer(
            order_id=order.id,
            customer_id=customer.id,
        )
        if existing and existing.status == INSTAGRAM_REWARD_STATUS_APPROVED:
            raise ConflictException("An approved Instagram reward already exists for this order")

        reward = existing
        if not reward:
            handle = (payload.instagram_username or "manual_claim").strip()
            if handle.startswith("@"):
                handle = handle[1:].strip()
            reward = await self.reward_repo.create_reward(
                customer_id=customer.id,
                order_id=order.id,
                instagram_username=handle or "manual_claim",
                instagram_post_url=payload.instagram_post_url,
            )
            await self.session.commit()

        if payload.auto_approve:
            approve_req = InstagramRewardApproveRequest(
                notes=payload.notes or "Manual staff entry claim",
                discount_percentage=payload.discount_percentage,
            )
            return await self.approve_reward(
                reward_id=reward.id,
                verifier=staff_user,
                payload=approve_req,
            )

        return await self._to_detail_response(reward)
