"""
ActivityLogModel — the shared house diary.

ONE log for all mutations: products, inventory, orders, employees,
media, taxonomy, offers, analytics, AI, and workforce.

Rule: never create a second log. Every domain appends here.

Record shape (from API_CONTRACT.md § Admin — Activity log):
  { id, at, actorEmployeeId, actorName, targetEmployeeId,
    targetProductId, targetOfferId, targetCategoryId,
    targetCollectionId, action, summary }
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ActivityLogModel(Base):
    """A single entry in the shared audit diary."""

    __tablename__ = "audit_activity_log"

    # ── Actor ─────────────────────────────────────────────────────────────────
    actor_employee_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    actor_name:        Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # ── Targets (at most one per entry is typically set) ─────────────────────
    target_employee_id:   Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    target_product_id:    Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    target_offer_id:      Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    target_category_id:   Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    target_collection_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    target_order_id:      Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    target_return_id:     Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    target_media_id:      Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    # ── Event ─────────────────────────────────────────────────────────────────
    action:  Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
