"""
Notification-related Pydantic schemas.

Implementable contract (API_CONTRACT.md → NOTIFICATIONS):

  GET  /admin/settings/notifications  → NotificationSettingsResponse
  PATCH /admin/settings/notifications → NotificationSettingsUpdate

Channel preference defaults from settingsRepository:
  { order: ["IN_APP"], returns: ["IN_APP"], employee: ["IN_APP"],
    lowStock: ["IN_APP"], offers: ["IN_APP"], marketing: [] }

Valid channel values: "IN_APP", "EMAIL", "SMS", "WHATSAPP"
"""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Allowed channel values
# ---------------------------------------------------------------------------

VALID_CHANNELS = {"IN_APP", "EMAIL", "SMS", "WHATSAPP"}

_CHANNEL_FIELD_DEFAULTS = {
    "order": ["IN_APP"],
    "returns": ["IN_APP"],
    "employee": ["IN_APP"],
    "low_stock": ["IN_APP"],
    "offers": ["IN_APP"],
    "marketing": [],
}


def _channel_list_field(default: list, description: str):
    return Field(default_factory=lambda: list(default), description=description)


# ---------------------------------------------------------------------------
# Core channel-preference schema
# ---------------------------------------------------------------------------


class NotificationChannelSettings(BaseModel):
    """
    Per-event-family channel list.
    Each list may contain zero or more of: "IN_APP", "EMAIL", "SMS", "WHATSAPP".
    """

    model_config = ConfigDict(populate_by_name=True)

    order: List[str] = _channel_list_field(
        ["IN_APP"],
        "Channels for order confirmation, status updates and cancellations.",
    )
    returns: List[str] = _channel_list_field(
        ["IN_APP"],
        "Channels for return approvals and refund notifications.",
    )
    employee: List[str] = _channel_list_field(
        ["IN_APP"],
        "Channels for employee-facing operational alerts.",
    )
    low_stock: List[str] = Field(
        default_factory=lambda: ["IN_APP"],
        alias="lowStock",
        description="Channels for low-stock threshold alerts.",
    )
    offers: List[str] = _channel_list_field(
        ["IN_APP"],
        "Channels for promotional offer notifications.",
    )
    marketing: List[str] = _channel_list_field(
        [],
        "Channels for marketing broadcast messages.",
    )


# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------


class NotificationSettingsResponse(BaseModel):
    """Response body for GET /admin/settings/notifications."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    ok: bool = True
    section: str = "notifications"
    settings: NotificationChannelSettings
    updated_by: Optional[str] = Field(
        default=None, alias="updatedBy", description="User id of the last editor."
    )
    updated_at: Optional[str] = Field(
        default=None, alias="updatedAt", description="ISO-8601 UTC timestamp."
    )


# ---------------------------------------------------------------------------
# Update / patch schema
# ---------------------------------------------------------------------------


class NotificationSettingsUpdate(BaseModel):
    """
    Request body for PATCH /admin/settings/notifications.
    All fields are optional — only supplied keys are merged.
    """

    model_config = ConfigDict(populate_by_name=True)

    order: Optional[List[str]] = None
    returns: Optional[List[str]] = None
    employee: Optional[List[str]] = None
    low_stock: Optional[List[str]] = Field(
        default=None, alias="lowStock"
    )
    offers: Optional[List[str]] = None
    marketing: Optional[List[str]] = None
