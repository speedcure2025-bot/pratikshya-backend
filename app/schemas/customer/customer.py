from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.schemas.customer.address import AddressResponse


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------

class PreferencesResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    email_notifications: bool = Field(True, serialization_alias="emailNotifications")
    sms_notifications: bool = Field(True, serialization_alias="smsNotifications")
    promotional_updates: bool = Field(True, serialization_alias="promotionalUpdates")
    order_updates: bool = Field(True, serialization_alias="orderUpdates")
    styling_invitations: bool = Field(True, serialization_alias="stylingInvitations")


class PreferencesUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    email_notifications: Optional[bool] = Field(None, alias="emailNotifications")
    sms_notifications: Optional[bool] = Field(None, alias="smsNotifications")
    promotional_updates: Optional[bool] = Field(None, alias="promotionalUpdates")
    order_updates: Optional[bool] = Field(None, alias="orderUpdates")
    styling_invitations: Optional[bool] = Field(None, alias="stylingInvitations")


# ---------------------------------------------------------------------------
# Session summary (for security.activeSessions)
# ---------------------------------------------------------------------------

class SessionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    ip_address: Optional[str] = Field(None, serialization_alias="ipAddress")
    user_agent: Optional[str] = Field(None, serialization_alias="userAgent")
    created_at: datetime = Field(serialization_alias="createdAt")
    expires_at: datetime = Field(serialization_alias="expiresAt")
    is_current: bool = Field(False, serialization_alias="isCurrent")


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

class ProfileResponse(BaseModel):
    """Customer-facing profile shape. Maps from UserModel + CustomerProfileModel."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    first_name: Optional[str] = Field(None, serialization_alias="firstName")
    last_name: Optional[str] = Field(None, serialization_alias="lastName")
    email: Optional[str] = None
    phone: Optional[str] = None
    date_of_birth: Optional[date] = Field(None, serialization_alias="dateOfBirth")
    avatar: Optional[str] = None
    loyalty_tier: str = Field("BRONZE", serialization_alias="loyaltyTier")
    loyalty_points: int = Field(0, serialization_alias="loyaltyPoints")
    created_at: datetime = Field(serialization_alias="createdAt")


class ProfileUpdate(BaseModel):
    """PATCH /customers/me — accepts any subset of fields."""

    model_config = ConfigDict(populate_by_name=True)

    first_name: Optional[str] = Field(None, alias="firstName", max_length=100)
    last_name: Optional[str] = Field(None, alias="lastName", max_length=100)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, max_length=20)
    date_of_birth: Optional[date] = Field(None, alias="dateOfBirth")
    avatar: Optional[str] = Field(None, max_length=1000)


# ---------------------------------------------------------------------------
# Full /customers/me response
# ---------------------------------------------------------------------------

class MeResponse(BaseModel):
    """
    GET /customers/me response envelope.
    Shape: { profile, addresses[], preferences, security: { activeSessions[] } }
    """

    model_config = ConfigDict(populate_by_name=True)

    ok: bool = True
    profile: ProfileResponse
    addresses: List[AddressResponse] = []
    preferences: PreferencesResponse
    security: dict  # { activeSessions: List[SessionSummary] }


# ---------------------------------------------------------------------------
# Admin customer list / detail response
# ---------------------------------------------------------------------------

class AdminCustomerResponse(BaseModel):
    """Customer record returned by admin endpoints, with derived stats."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    first_name: Optional[str] = Field(None, serialization_alias="firstName")
    last_name: Optional[str] = Field(None, serialization_alias="lastName")
    email: Optional[str] = None
    phone: Optional[str] = None
    status: str
    loyalty_tier: str = Field(serialization_alias="loyaltyTier")
    loyalty_points: int = Field(serialization_alias="loyaltyPoints")
    created_at: datetime = Field(serialization_alias="createdAt")
    # Derived / joined
    order_count: int = Field(0, serialization_alias="orderCount")
    lifetime_spend: float = Field(0.0, serialization_alias="lifetimeSpend")
    addresses: List[AddressResponse] = []


class AdminCustomerListResponse(BaseModel):
    ok: bool = True
    customers: List[AdminCustomerResponse]
    total: int
