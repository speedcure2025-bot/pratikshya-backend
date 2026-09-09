"""
WishlistModel — persisted server-side wishlist for authenticated customers.

One wishlist per customer. A wishlist is simply a set of product IDs.
Guest wishlists remain client-only (per spec — no merge path is defined).
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class WishlistModel(Base):
    """Server-side wishlist belonging to a single customer."""

    __tablename__ = "commerce_wishlist"

    # One wishlist per customer
    customer_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Relationships
    items: Mapped[list["WishlistItemModel"]] = relationship(
        "WishlistItemModel",
        back_populates="wishlist",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
