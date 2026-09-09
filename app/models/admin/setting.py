"""
Admin Setting model — stores per-section JSON configuration.

Schema: DATABASE_SCHEMA.md §41
  id (section)  PK  — one of the 19 recognised setting keys (e.g. "notifications")
  value         JSONB — deep-merged against SETTINGS_DEFAULTS on read
  updated_by    string  — user id of the last editor
  updated_at    UTC timestamp

The `id` column is intentionally declared as a plain String(64) without a
UUID default — section names ARE the primary key.  SQLAlchemy 2.x allows a
subclass to redeclare a column originally defined on Base, which replaces the
base-class column definition in the subclass's __table__.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SettingModel(Base):
    """
    One row per configuration section.
    Primary key is the section name (e.g. "notifications"), not a UUID.
    """

    __tablename__ = "admin_setting"

    # Override Base.id — section name is the natural PK, no UUID default.
    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        index=True,
        comment="Setting section key, e.g. 'notifications', 'shipping'.",
    )

    # JSON value column (falls back to TEXT on SQLite for local dev / tests)
    value: Mapped[dict] = mapped_column(
        JSONB().with_variant(Text(), "sqlite"),
        nullable=False,
        default=dict,
        comment="Section configuration stored as JSONB.",
    )

    updated_by: Mapped[Optional[str]] = mapped_column(
        String(36), nullable=True, comment="User id of the last editor."
    )

    # Timestamps — keep in sync with Base but declared explicitly so the
    # column appears in the table even though Base already defines them.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
