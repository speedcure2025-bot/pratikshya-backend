"""
Product — SQLAlchemy model.

Mirrors the full `product` table shape from DATABASE_SCHEMA.md §7.
JSONB columns use JSON type (SQLAlchemy handles Postgres JSONB transparently).
"""

from typing import Optional
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ProductModel(Base):
    """Full product catalogue record."""

    __tablename__ = "catalog_product"

    # ── Identity ────────────────────────────────────────────────────────────
    # id (PK) inherited from Base — used as the permanent product id (pf-001, KID-007)
    product_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="Mirror of id; UI stable label."
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    slug: Mapped[str] = mapped_column(String(255), nullable=False, default="", index=True)
    sku: Mapped[str] = mapped_column(String(100), nullable=False, default="", index=True)
    brand: Mapped[str] = mapped_column(String(100), nullable=False, default="Pratikshya Fashon")
    product_type: Mapped[str] = mapped_column(String(50), nullable=False, default="fashion")
    product_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default="")
    barcode: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default="")
    internal_reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default="")

    # ── Placement ────────────────────────────────────────────────────────────
    category: Mapped[str] = mapped_column(String(100), nullable=False, default="", index=True)
    subcategory: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default="")
    gender: Mapped[str] = mapped_column(String(20), nullable=False, default="Women")

    # ── Content ──────────────────────────────────────────────────────────────
    short_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default="")
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default="")
    highlights: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True, default=list)
    specifications: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True, default=dict)
    care_instructions: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True, default=list)
    delivery_info: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default="")
    return_info: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default="")
    return_policy: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True, default=dict)

    # ── Attributes ───────────────────────────────────────────────────────────
    fabric: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default="")
    material: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default="")
    primary_color: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default="")
    secondary_color: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default="")
    colors: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True, default=list)
    patterns: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True, default=list)
    work: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True, default=list)
    occasion: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True, default=list)
    sizes: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True, default=list)
    unavailable_colors: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True, default=list)
    unavailable_sizes: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True, default=list)
    season: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, default="")
    fit: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, default="")
    length: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, default="")

    # ── Merchandising ────────────────────────────────────────────────────────
    collection: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, default="")
    collections: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True, default=list)
    tags: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True, default=list)
    badges: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True, default=list)
    is_featured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_bestseller: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_new: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_limited_edition: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_trending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    flags: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True, default=dict)

    # ── Pricing ──────────────────────────────────────────────────────────────
    price: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    original_price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    compare_at_price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    pricing: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True, default=dict)

    # ── Inventory snapshot ───────────────────────────────────────────────────
    stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    availability: Mapped[str] = mapped_column(String(30), nullable=False, default="in-stock")
    inventory_tracked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    low_stock_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=5)

    # ── Ratings (authored) ───────────────────────────────────────────────────
    rating: Mapped[Optional[float]] = mapped_column(Numeric(3, 2), nullable=True)
    review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── SEO ──────────────────────────────────────────────────────────────────
    seo: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True, default=dict)

    # ── Publishing & workflow ─────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="DRAFT", index=True
    )
    published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    review: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True, default=dict)
    assigned_employee_id: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, index=True
    )
    review_flags: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True, default=list)

    # ── Media claims ─────────────────────────────────────────────────────────
    media_ids: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True, default=list)
    primary_media_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    gallery_media_ids: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True, default=list)
    # Legacy authored fields
    image: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default="")
    hover_image: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default="")
    additional_images: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True, default=list)

    # ── Audit ────────────────────────────────────────────────────────────────
    created_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    updated_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    published_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    published_at: Mapped[Optional[DateTime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    history: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True, default=list)
    price_history: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True, default=list)

    __table_args__ = (
        Index("ix_catalog_product_category_status", "category", "status"),
        Index("ix_catalog_product_status", "status"),
        Index("ix_catalog_product_slug", "slug"),
        Index("ix_catalog_product_sku", "sku"),
        Index("ix_catalog_product_assigned_employee", "assigned_employee_id"),
    )
