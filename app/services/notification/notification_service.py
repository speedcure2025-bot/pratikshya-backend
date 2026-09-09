"""
NotificationSettingsService — business logic for admin notification settings.

Implements:
  GET  /admin/settings/notifications
  PATCH /admin/settings/notifications

Storage:
  Uses the `admin_setting` table (SettingModel).
  The `id` column is the section key — "notifications".
  `value` is a JSONB column holding the channel-preference dict.
  If no row exists yet the service falls back to SETTINGS_DEFAULTS
  and upserts on the first PATCH.
"""

import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin.setting import SettingModel
from app.schemas.notification.notification import (
    VALID_CHANNELS,
    NotificationChannelSettings,
    NotificationSettingsUpdate,
)

# Section key used as the PK in admin_setting
SECTION_KEY = "notifications"

# Defaults that mirror the frontend's settingsRepository defaults
SETTINGS_DEFAULTS: dict = {
    "order": ["IN_APP"],
    "returns": ["IN_APP"],
    "employee": ["IN_APP"],
    "lowStock": ["IN_APP"],
    "offers": ["IN_APP"],
    "marketing": [],
}


class NotificationSettingsService:
    """CRUD layer for the notification section of admin_setting."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _load_row(self) -> Optional[SettingModel]:
        stmt = select(SettingModel).where(SettingModel.id == SECTION_KEY)
        res = await self.db.execute(stmt)
        return res.scalars().first()

    def _row_to_schema(self, row: Optional[SettingModel]) -> NotificationChannelSettings:
        """
        Convert a DB row (or None) into a validated NotificationChannelSettings.
        Falls back to defaults for any missing key.
        """
        raw: dict = dict(SETTINGS_DEFAULTS)
        if row and row.value:
            # row.value may be a dict (JSONB) or a JSON string (SQLite / test env)
            stored = row.value if isinstance(row.value, dict) else json.loads(row.value)
            raw.update(stored)

        return NotificationChannelSettings(
            order=raw.get("order", SETTINGS_DEFAULTS["order"]),
            returns=raw.get("returns", SETTINGS_DEFAULTS["returns"]),
            employee=raw.get("employee", SETTINGS_DEFAULTS["employee"]),
            low_stock=raw.get("lowStock", SETTINGS_DEFAULTS["lowStock"]),
            offers=raw.get("offers", SETTINGS_DEFAULTS["offers"]),
            marketing=raw.get("marketing", SETTINGS_DEFAULTS["marketing"]),
        )

    @staticmethod
    def _validate_channels(channels: list[str], field: str) -> None:
        invalid = [ch for ch in channels if ch not in VALID_CHANNELS]
        if invalid:
            raise ValueError(
                f"Invalid channel(s) for '{field}': {invalid}. "
                f"Allowed values: {sorted(VALID_CHANNELS)}"
            )

    # ------------------------------------------------------------------
    # GET /admin/settings/notifications
    # ------------------------------------------------------------------

    async def get_settings(self) -> tuple[NotificationChannelSettings, Optional[SettingModel]]:
        """Return the current notification channel settings with raw row."""
        row = await self._load_row()
        return self._row_to_schema(row), row

    # ------------------------------------------------------------------
    # PATCH /admin/settings/notifications
    # ------------------------------------------------------------------

    async def update_settings(
        self,
        data: NotificationSettingsUpdate,
        updated_by: str,
    ) -> tuple[NotificationChannelSettings, SettingModel]:
        """
        Deep-merge the supplied fields into the stored value.
        Validates channel strings before writing.
        Creates the row on first call (upsert pattern).
        """
        row = await self._load_row()

        # Build current stored dict — start from defaults, overlay stored value
        current: dict = dict(SETTINGS_DEFAULTS)
        if row and row.value:
            stored = row.value if isinstance(row.value, dict) else json.loads(row.value)
            current.update(stored)

        # Apply patch fields — only overwrite keys that were explicitly supplied
        updates = data.model_dump(by_alias=True, exclude_none=True)

        for api_key, value in updates.items():
            # Validate each channel list
            self._validate_channels(value, api_key)
            current[api_key] = value

        if row is None:
            # First write — create the row
            row = SettingModel(
                id=SECTION_KEY,
                value=current,
                updated_by=updated_by,
                updated_at=datetime.now(timezone.utc),
            )
            self.db.add(row)
        else:
            row.value = current
            row.updated_by = updated_by
            row.updated_at = datetime.now(timezone.utc)

        await self.db.flush()
        await self.db.refresh(row)

        return self._row_to_schema(row), row
