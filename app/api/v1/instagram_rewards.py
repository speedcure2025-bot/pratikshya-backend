"""
Instagram Rewards API Router.

Endpoints for customer submission & status history alongside admin/staff verification & coupon generation:
  Customer:
    POST /customer/instagram-rewards        Submit reward request
    GET  /customer/instagram-rewards        View reward submission history

  Admin / Staff (RBAC protected):
    GET  /admin/instagram-rewards           List & filter all submissions
    GET  /admin/instagram-rewards/{id}      View complete submission detail
    POST /admin/instagram-rewards/{id}/approve Approve claim & generate coupon
    POST /admin/instagram-rewards/{id}/reject  Reject claim with feedback
"""

from typing import Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenException
from app.dependencies import (
    get_current_customer,
    get_current_user,
    get_db,
    require_staff_permission,
)
from app.models.auth.user import UserModel
from app.schemas.commerce.instagram_reward import (
    InstagramRewardApproveRequest,
    InstagramRewardDetailResponse,
    InstagramRewardListResponse,
    InstagramRewardManualClaimRequest,
    InstagramRewardRejectRequest,
    InstagramRewardReopenRequest,
    InstagramRewardSubmitRequest,
)
from app.schemas.common import DataResponse
from app.services.commerce.instagram_reward_service import InstagramRewardService

router = APIRouter(tags=["Instagram Rewards"])


# ── Dependency: Staff User Guard ─────────────────────────────────────────────

async def get_current_staff_member(
    user: UserModel = Depends(get_current_user),
) -> UserModel:
    """Ensure current user is authenticated as an Admin or Employee."""
    if user.user_type not in ("admin", "employee"):
        raise ForbiddenException("Staff authentication privileges required.")
    return user


# =============================================================================
# Customer Endpoints
# =============================================================================

@router.post(
    "/customer/instagram-rewards",
    response_model=DataResponse[InstagramRewardDetailResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Customer — Submit Instagram reward request",
    description=(
        "Submits an Instagram post/story link and order ID for verification. "
        "The order must belong to the authenticated customer and be in a paid/completed state."
    ),
)
async def submit_instagram_reward(
    payload: InstagramRewardSubmitRequest,
    current_customer: UserModel = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    service = InstagramRewardService(db)
    result = await service.submit_reward(customer=current_customer, payload=payload)
    return DataResponse(
        success=True,
        message="Instagram reward submission received and pending verification.",
        data=result,
    )


@router.get(
    "/customer/instagram-rewards",
    response_model=DataResponse[InstagramRewardListResponse],
    summary="Customer — View reward submission history",
    description="Returns a paginated list of reward requests submitted by the authenticated customer.",
)
async def get_customer_instagram_rewards(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    current_customer: UserModel = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    service = InstagramRewardService(db)
    result = await service.get_customer_rewards(
        customer_id=current_customer.id,
        page=page,
        limit=limit,
    )
    return DataResponse(
        success=True,
        message="Customer Instagram reward history retrieved successfully.",
        data=result,
    )


# =============================================================================
# Admin / Staff Endpoints
# =============================================================================

@router.get(
    "/admin/instagram-rewards",
    response_model=DataResponse[InstagramRewardListResponse],
    summary="Admin — List reward requests",
    description=(
        "Retrieves a paginated list of Instagram reward submissions. "
        "Supports filtering by status (PENDING, APPROVED, REJECTED), customer, order, or search query. "
        "Requires Super Admin, Admin, or Employee with offer management permissions."
    ),
)
async def admin_list_instagram_rewards(
    status_filter: Optional[str] = Query(
        None, alias="status", description="Filter by status: PENDING | APPROVED | REJECTED"
    ),
    customer: Optional[str] = Query(None, description="Filter by customer ID"),
    order: Optional[str] = Query(None, description="Filter by order ID"),
    search: Optional[str] = Query(None, description="Search handle or order ID"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(50, ge=1, le=200, description="Items per page"),
    current_user: UserModel = Depends(get_current_staff_member),
    db: AsyncSession = Depends(get_db),
):
    await require_staff_permission(current_user, db, "instagram_reward.manage")
    service = InstagramRewardService(db)
    result = await service.list_rewards(
        status=status_filter,
        customer_id=customer,
        order_id=order,
        search=search,
        page=page,
        limit=limit,
    )
    return DataResponse(
        success=True,
        message="Instagram reward submissions retrieved successfully.",
        data=result,
    )


@router.get(
    "/admin/instagram-rewards/{reward_id}",
    response_model=DataResponse[InstagramRewardDetailResponse],
    summary="Admin — View reward details",
    description="Retrieves complete detail for a single Instagram reward submission.",
)
async def admin_get_instagram_reward_detail(
    reward_id: str,
    current_user: UserModel = Depends(get_current_staff_member),
    db: AsyncSession = Depends(get_db),
):
    await require_staff_permission(current_user, db, "instagram_reward.manage")
    service = InstagramRewardService(db)
    result = await service.get_reward_by_id(reward_id)
    return DataResponse(
        success=True,
        message="Instagram reward details retrieved successfully.",
        data=result,
    )


@router.post(
    "/admin/instagram-rewards/{reward_id}/approve",
    response_model=DataResponse[InstagramRewardDetailResponse],
    summary="Admin — Approve reward & generate coupon",
    description=(
        "Verifies a PENDING Instagram reward claim and generates a customer-bound, "
        "one-time percentage discount coupon based on INSTAGRAM_REWARD_DISCOUNT_PERCENT configuration. "
        "Atomic transaction with pessimistic SELECT FOR UPDATE row locking."
    ),
)
async def admin_approve_instagram_reward(
    reward_id: str,
    payload: Optional[InstagramRewardApproveRequest] = None,
    current_user: UserModel = Depends(get_current_staff_member),
    db: AsyncSession = Depends(get_db),
):
    await require_staff_permission(current_user, db, "instagram_reward.manage")
    service = InstagramRewardService(db)
    result = await service.approve_reward(
        reward_id=reward_id,
        verifier=current_user,
        payload=payload,
    )
    return DataResponse(
        success=True,
        message="Instagram reward approved and unique customer coupon generated successfully.",
        data=result,
    )


@router.post(
    "/admin/instagram-rewards/{reward_id}/reject",
    response_model=DataResponse[InstagramRewardDetailResponse],
    summary="Admin — Reject reward request",
    description="Rejects a PENDING Instagram reward claim with staff feedback reason.",
)
async def admin_reject_instagram_reward(
    reward_id: str,
    payload: InstagramRewardRejectRequest,
    current_user: UserModel = Depends(get_current_staff_member),
    db: AsyncSession = Depends(get_db),
):
    await require_staff_permission(current_user, db, "instagram_reward.manage")
    service = InstagramRewardService(db)
    result = await service.reject_reward(
        reward_id=reward_id,
        verifier=current_user,
        payload=payload,
    )
    return DataResponse(
        success=True,
        message="Instagram reward rejected successfully.",
        data=result,
    )


@router.post(
    "/admin/instagram-rewards/{reward_id}/reopen",
    response_model=DataResponse[InstagramRewardDetailResponse],
    summary="Admin — Re-open rejected reward back to pending",
    description="Re-opens a REJECTED Instagram reward submission back to PENDING for re-review.",
)
async def admin_reopen_instagram_reward(
    reward_id: str,
    current_user: UserModel = Depends(get_current_staff_member),
    db: AsyncSession = Depends(get_db),
):
    await require_staff_permission(current_user, db, "instagram_reward.manage")
    service = InstagramRewardService(db)
    result = await service.reopen_reward(
        reward_id=reward_id,
        verifier=current_user,
    )
    return DataResponse(
        success=True,
        message="Reward re-opened to Pending for re-review.",
        data=result,
    )


@router.post(
    "/admin/instagram-rewards/manual-claim",
    response_model=DataResponse[InstagramRewardDetailResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Admin — Manually add/issue customer reward",
    description=(
        "Manually creates an Instagram reward claim for a customer by User ID/Email and Order ID/Number. "
        "Can automatically approve and issue the reward coupon immediately."
    ),
)
async def admin_manual_claim_instagram_reward(
    payload: InstagramRewardManualClaimRequest,
    current_user: UserModel = Depends(get_current_staff_member),
    db: AsyncSession = Depends(get_db),
):
    await require_staff_permission(current_user, db, "instagram_reward.manage")
    service = InstagramRewardService(db)
    result = await service.create_manual_claim(
        staff_user=current_user,
        payload=payload,
    )
    return DataResponse(
        success=True,
        message="Customer reward manually created and processed successfully.",
        data=result,
    )
