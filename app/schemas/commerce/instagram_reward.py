"""
Instagram Reward Pydantic Schemas

Defines request and response schemas for the Instagram Customer Reward Coupon feature:
  - Customer submission request
  - Admin reward list & detail responses
  - Staff approval & rejection requests
  - Generated reward coupon response
"""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── 1. Customer Reward Submission Request ────────────────────────────────────

class InstagramRewardSubmitRequest(BaseModel):
    """
    Schema for customer submitting an Instagram reward claim.
    
    Strictly contains ONLY fields the customer is allowed to provide.
    Internal fields (customer_id, status, verified_by, coupon details, etc.)
    are populated exclusively by the backend service.
    """
    order_id: str = Field(
        ...,
        description="ID of the completed purchase order",
        examples=["550e8400-e29b-41d4-a716-446655440000"],
    )
    instagram_username: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Customer's Instagram handle (leading '@' is stripped automatically)",
        examples=["jane_doe_fashion"],
    )
    instagram_post_url: Optional[str] = Field(
        None,
        max_length=500,
        description="Direct link to the Instagram post or story (optional but recommended)",
        examples=["https://www.instagram.com/p/C123456789/"],
    )

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("order_id", mode="before")
    @classmethod
    def validate_order_id(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
            if not v:
                raise ValueError("order_id cannot be empty")
        return v

    @field_validator("instagram_username", mode="before")
    @classmethod
    def sanitize_instagram_username(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("@"):
                v = v[1:].strip()
            if not v:
                raise ValueError("instagram_username cannot be empty or just '@'")
        return v

    @field_validator("instagram_post_url", mode="before")
    @classmethod
    def sanitize_post_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return None
            if not (v.startswith("http://") or v.startswith("https://")):
                raise ValueError("instagram_post_url must start with http:// or https://")
        return v


# ── 2. Generated Reward Coupon Response ──────────────────────────────────────

class InstagramRewardCouponResponse(BaseModel):
    """
    Schema representing the one-time discount coupon generated upon reward approval.
    """
    id: str
    code: str
    discount_percentage: float = Field(..., description="Discount percentage (e.g., 10.0 for 10%)")
    customer_id: str
    is_used: bool = False
    usage_limit: int = 1
    times_used: int = 0
    valid_until: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


# ── 3. Reward Detail Response ─────────────────────────────────────────────────

class InstagramRewardDetailResponse(BaseModel):
    """
    Full detail representation of an Instagram reward submission.
    Used for both single detail views and list items.
    """
    id: str
    customer_id: str
    customer_name: Optional[str] = None
    customer_email: Optional[str] = None
    order_id: str
    order_number: Optional[str] = None
    instagram_username: str
    instagram_post_url: Optional[str] = None
    status: str = Field(..., description="PENDING | APPROVED | REJECTED")
    verified_by: Optional[str] = None
    verifier_name: Optional[str] = None
    verified_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    coupon_id: Optional[str] = None
    coupon_code: Optional[str] = None
    coupon: Optional[InstagramRewardCouponResponse] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


# ── 4. Admin Reward List Response ─────────────────────────────────────────────

class InstagramRewardListResponse(BaseModel):
    """
    Paginated response for admin/staff listing reward submissions.
    """
    items: List[InstagramRewardDetailResponse]
    total: int = Field(..., ge=0, description="Total number of matching submissions")
    page: int = Field(..., ge=1, description="Current page number")
    limit: int = Field(..., ge=1, description="Number of items per page")
    total_pages: int = Field(..., ge=0, description="Total number of pages")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


# ── 5. Approve Reward Request ─────────────────────────────────────────────────

class InstagramRewardApproveRequest(BaseModel):
    """
    Request body for authorized staff/admin approving an Instagram reward claim.
    """
    discount_percentage: float = Field(
        ...,
        gt=0,
        le=100,
        description="Discount percentage for this coupon (e.g. 15 for 15%). Required — admin must decide.",
    )
    notes: Optional[str] = Field(
        None,
        max_length=500,
        description="Optional internal verification notes entered by staff",
    )

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("notes", mode="before")
    @classmethod
    def sanitize_notes(cls, v: Optional[str]) -> Optional[str]:
        if isinstance(v, str):
            v = v.strip()
            return v if v else None
        return v


# ── 6. Reject Reward Request ──────────────────────────────────────────────────

class InstagramRewardRejectRequest(BaseModel):
    """
    Request body for authorized staff/admin rejecting an Instagram reward claim.
    """
    reason: str = Field(
        ...,
        min_length=3,
        max_length=1000,
        description="Mandatory staff reason for rejecting the submission",
        examples=["Tagged account is incorrect or post does not display product."],
    )

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("reason", mode="before")
    @classmethod
    def validate_reason(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
            if len(v) < 3:
                raise ValueError("Rejection reason must be at least 3 characters long")
        return v


# ── 7. Staff Manual Reward Entry Request ─────────────────────────────────────

class InstagramRewardManualClaimRequest(BaseModel):
    """
    Request body for authorized staff/admin manually adding a customer reward.
    """
    customer_identifier: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Customer User ID or Email address",
        examples=["user-12345", "customer@example.com"],
    )
    order_identifier: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Order ID or Order Number",
        examples=["ord-12345", "PF-0001"],
    )
    instagram_username: Optional[str] = Field(
        "manual_claim",
        max_length=100,
        description="Customer's Instagram handle (optional)",
    )
    instagram_post_url: Optional[str] = Field(
        None,
        max_length=500,
        description="Direct link to Instagram post (optional)",
    )
    auto_approve: bool = Field(
        True,
        description="If True, immediately approve and issue reward coupon",
    )
    notes: Optional[str] = Field(
        None,
        max_length=500,
        description="Optional internal verification notes entered by staff",
    )
    discount_percentage: float = Field(
        ...,
        gt=0,
        le=100,
        description="Discount percentage for this coupon. Required — admin must decide.",
    )

    model_config = ConfigDict(populate_by_name=True)

# ── 8. Staff Re-open Rejected Reward Request ─────────────────────────────────

class InstagramRewardReopenRequest(BaseModel):
    """
    Request body for authorized staff re-opening a REJECTED reward back to PENDING.
    """
    notes: Optional[str] = Field(
        None,
        max_length=500,
        description="Optional notes explaining why this rejection is being re-opened",
    )

    model_config = ConfigDict(populate_by_name=True)

