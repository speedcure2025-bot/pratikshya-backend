"""
Marketing media — database model.

HOME_HERO and other editorial/promotion plates belong to MARKETING MEDIA,
not product media. This table is the source of truth for backend-managed
hero curation.

Distinction enforced by design:
  PRODUCT MEDIA      = products/{PRODUCT_ID}/{file}  → ProductMediaModel
  COLLECTION/EDITORIAL = collections/...               → filesystem / legacy
  MARKETING/HERO     = hero/... or marketing/...      → MarketingMediaModel

Each row is one placement entry (e.g. HOME_HERO position 1) referencing a
stored object key (hero/hero001.avif) and optional copy (title, subtitle,
cta). Ordering is explicit via sort_order, activation via is_active, and
duplicate prevention via unique (placement, object_key).
"""

from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MarketingMediaModel(Base):
    """Backend-managed marketing media placement entry."""

    __tablename__ = "media_marketing_media"

    # ── Placement ────────────────────────────────────────────────────────
    placement: Mapped[str] = mapped_column(
        String(50), nullable=False, default="HOME_HERO", index=True
    )

    # ── Storage reference ────────────────────────────────────────────────
    # Object key in the configured store, e.g. hero/hero001.avif or
    # marketing/campaign/festive.avif. The file itself lives in
    # storage/media (local provider) or S3; this row stores metadata only.
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)

    # Optional FK to the durable media_asset row (if the object was
    # registered via /media/register). Nullable so a marketing entry can be
    # created directly from an existing hero file without first registering
    # a media_asset row — the migration CLI copies files into storage/media
    # and they are addressable by key alone.
    media_asset_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("media_media_asset.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Editorial copy for hero slides ───────────────────────────────────
    title: Mapped[Optional[str]] = mapped_column(String(255))
    subtitle: Mapped[Optional[str]] = mapped_column(String(500))
    cta_label: Mapped[Optional[str]] = mapped_column(String(100))
    cta_href: Mapped[Optional[str]] = mapped_column(String(500))
    alt_text: Mapped[Optional[str]] = mapped_column(Text)

    # ── Ordering / activation ────────────────────────────────────────────
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # ── Audit ────────────────────────────────────────────────────────────
    created_by: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "placement", "object_key", name="uq_marketing_media_placement_object_key"
        ),
        Index("ix_marketing_media_placement_active_sort", "placement", "is_active", "sort_order"),
        Index("ix_marketing_media_placement_sort", "placement", "sort_order"),
    )
