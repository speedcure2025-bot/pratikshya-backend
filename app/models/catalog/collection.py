"""
Collection — SQLAlchemy model.

Statuses : DRAFT | SCHEDULED | ACTIVE | PAUSED | EXPIRED | ARCHIVED
Types    : MANUAL (explicit productIds) | RULE_BASED (rule: flag | occasion | fabricIncludes)

displayStatus is derived server-side from (status, startDate, endDate) and is
never stored as a column — it is computed in the service layer.

Membership logic (see API_CONTRACT.md § COLLECTIONS):
  MANUAL   → explicit_product_ids list
  RULE_BASED → rule JSONB evaluated against product fields at query time
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CollectionModel(Base):
    """Database model for Collection."""

    __tablename__ = "catalog_collection"

    # ── Identity ──────────────────────────────────────────────────────────────
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(220), nullable=False, unique=True, index=True)

    # ── Display ───────────────────────────────────────────────────────────────
    eyebrow: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, default="")
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default="")
    image: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default="")
    hero_media_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    thumbnail_media_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # ── Classification ────────────────────────────────────────────────────────
    # type: MANUAL | RULE_BASED
    type: Mapped[str] = mapped_column(String(30), nullable=False, default="MANUAL")

    # ── Scheduling ────────────────────────────────────────────────────────────
    start_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    end_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Workflow / status ─────────────────────────────────────────────────────
    # DRAFT | SCHEDULED | ACTIVE | PAUSED | EXPIRED | ARCHIVED
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="DRAFT", index=True
    )

    # ── Merchandising ─────────────────────────────────────────────────────────
    featured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── Membership ────────────────────────────────────────────────────────────
    # MANUAL: list of explicit product IDs.
    explicit_product_ids: Mapped[Optional[list]] = mapped_column(
        JSONB, nullable=True, default=list
    )

    # RULE_BASED: rule shape { "flag"?: str, "occasion"?: str, "fabricIncludes"?: str }
    rule: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True, default=dict)

    # ── Audit ─────────────────────────────────────────────────────────────────
    created_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    updated_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_catalog_collection_status", "status"),
        Index("ix_catalog_collection_sort_order", "sort_order"),
        Index("ix_catalog_collection_type", "type"),
    )
