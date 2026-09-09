"""
Category & Subcategory — SQLAlchemy models.

Mirrors the taxonomy shape from data/taxonomy.json and API_CONTRACT.md.

Statuses
  Category   : DRAFT | ACTIVE | ARCHIVED
  Subcategory: DRAFT | ACTIVE | ARCHIVED

Critical: Archiving a category removes ALL its products from every
customer surface (the visibility gate checks category status).
"""

from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class CategoryModel(Base):
    """Top-level product category (e.g. sarees, lehengas)."""

    __tablename__ = "catalog_category"

    # ── Identity ──────────────────────────────────────────────────────────────
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)

    # ── Display ───────────────────────────────────────────────────────────────
    eyebrow: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default="")
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default="")
    image: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default="")
    banner_media_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # ── Merchandising ─────────────────────────────────────────────────────────
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    featured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── SEO ───────────────────────────────────────────────────────────────────
    seo_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, default="")
    seo_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default="")

    # ── Workflow ──────────────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="DRAFT", index=True
    )

    # ── Audit ─────────────────────────────────────────────────────────────────
    created_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    updated_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # ── Relationships ─────────────────────────────────────────────────────────
    subcategories: Mapped[list["SubcategoryModel"]] = relationship(
        "SubcategoryModel",
        back_populates="category",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_catalog_category_status", "status"),
        Index("ix_catalog_category_sort_order", "sort_order"),
    )


class SubcategoryModel(Base):
    """Subcategory scoped to a parent category.

    ID convention: "<categoryId>-<slug>", e.g. "sarees-pato-saree".
    Slugs are unique within a category (not globally).
    """

    __tablename__ = "catalog_subcategory"

    # ── Parent ────────────────────────────────────────────────────────────────
    category_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("catalog_category.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Identity ──────────────────────────────────────────────────────────────
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, index=True)

    # ── Display ───────────────────────────────────────────────────────────────
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default="")
    image: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default="")

    # ── Merchandising ─────────────────────────────────────────────────────────
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── Workflow ──────────────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="DRAFT", index=True
    )

    # ── Audit ─────────────────────────────────────────────────────────────────
    created_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    updated_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # ── Relationships ─────────────────────────────────────────────────────────
    category: Mapped["CategoryModel"] = relationship(
        "CategoryModel", back_populates="subcategories"
    )

    __table_args__ = (
        # slug unique per category
        Index("uq_subcategory_category_slug", "category_id", "slug", unique=True),
        Index("ix_catalog_subcategory_status", "status"),
    )
