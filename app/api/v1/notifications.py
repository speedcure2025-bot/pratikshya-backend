"""
NOTIFICATIONS section — API_CONTRACT.md → NOTIFICATIONS

Implementable endpoints (only 2 exist per the contract):

  GET  /admin/settings/notifications  → get_notification_settings
  PATCH /admin/settings/notifications → update_notification_settings

Not built (no frontend consumer, explicitly deferred in the contract):
  - GET /notifications (inbox)
  - POST /notifications/{id}/read
  - Unread counts
  - Email/SMS delivery templates

PATCH /customers/me/preferences (the 5 customer notification booleans)
is already implemented in customers.py — not duplicated here.
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessLogicException, ForbiddenException
from app.core.logging import get_logger
from app.dependencies import get_current_user, get_db, require_permission_for_user
from app.models.auth.user import UserModel
from app.schemas.notification.notification import (
    NotificationSettingsResponse,
    NotificationSettingsUpdate,
    NotificationChannelSettings,
)
from app.services.notification.notification_service import NotificationSettingsService

logger = get_logger(__name__)

router = APIRouter(tags=["Notifications"])


# ──────────────────────────────────────────────────────────────────────────────
# GET /admin/settings/notifications
# ──────────────────────────────────────────────────────────────────────────────


@router.get(
    "/admin/settings/notifications",
    response_model=NotificationSettingsResponse,
    summary="[Admin] Get notification channel settings",
    description=(
        "Returns the per-event-family channel preferences stored in the "
        "`admin_setting` table under section `notifications`.  \n\n"
        "Channel values: `IN_APP`, `EMAIL`, `SMS`, `WHATSAPP`.  \n"
        "Falls back to defaults if the row has never been written:  \n"
        "`{ order: [\"IN_APP\"], returns: [\"IN_APP\"], employee: [\"IN_APP\"], "
        "lowStock: [\"IN_APP\"], offers: [\"IN_APP\"], marketing: [] }`.  \n\n"
        "**Authorization**: Admin or employee with `settings.view` privilege."
    ),
    status_code=status.HTTP_200_OK,
)
async def get_notification_settings(
    current_user: UserModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Only admin and employee users with settings.view may read admin settings
    if current_user.user_type not in ("admin", "employee"):
        raise ForbiddenException("Admin or employee authentication required.")
    await require_permission_for_user(current_user, db, "settings.view")

    service = NotificationSettingsService(db)
    channel_settings, row = await service.get_settings()

    return NotificationSettingsResponse(
        ok=True,
        section="notifications",
        settings=channel_settings,
        updatedBy=row.updated_by if row else None,
        updatedAt=row.updated_at.isoformat() if row and row.updated_at else None,
    )


# ──────────────────────────────────────────────────────────────────────────────
# PATCH /admin/settings/notifications
# ──────────────────────────────────────────────────────────────────────────────


@router.patch(
    "/admin/settings/notifications",
    response_model=NotificationSettingsResponse,
    summary="[Admin] Update notification channel settings",
    description=(
        "Deep-merges the supplied channel lists into the stored settings.  \n"
        "Only keys present in the request body are updated; omitted keys keep "
        "their current values.  \n\n"
        "**Body** (all fields optional):  \n"
        "```json\n"
        "{\n"
        '  "order":    ["IN_APP", "EMAIL"],\n'
        '  "returns":  ["IN_APP"],\n'
        '  "employee": ["IN_APP"],\n'
        '  "lowStock": ["IN_APP", "SMS"],\n'
        '  "offers":   ["IN_APP"],\n'
        '  "marketing": []\n'
        "}\n"
        "```\n\n"
        "Valid channel values: `IN_APP`, `EMAIL`, `SMS`, `WHATSAPP`.  \n"
        "Passing an unrecognised channel returns HTTP 422.  \n\n"
        "**Authorization**: Admin only (`settings.edit`)."
    ),
    status_code=status.HTTP_200_OK,
)
async def update_notification_settings(
    data: NotificationSettingsUpdate,
    current_user: UserModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Only admins with settings.edit may write admin settings
    if current_user.user_type != "admin":
        raise ForbiddenException("Admin authentication required to edit settings.")
    await require_permission_for_user(current_user, db, "settings.edit")

    service = NotificationSettingsService(db)
    try:
        channel_settings, row = await service.update_settings(
            data, updated_by=current_user.id
        )
    except ValueError as exc:
        # Channel validation error → surface as BusinessLogicException (422)
        raise BusinessLogicException(str(exc))

    return NotificationSettingsResponse(
        ok=True,
        section="notifications",
        settings=channel_settings,
        updatedBy=row.updated_by,
        updatedAt=row.updated_at.isoformat() if row.updated_at else None,
    )
