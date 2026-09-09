"""
WishlistItemModel — one product saved to a customer's wishlist.

Identity is product-based: each product_id can appear at most once per wishlist.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class WishlistItemModel(Base):
    """A single product saved in a customer wishlist."""

    __tablename__ = "commerce_wishlist_item"
    __table_args__ = (
        UniqueConstraint("wishlist_id", "product_id", name="uq_wishlist_product"),
    )

    wishlist_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("commerce_wishlist.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    product_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    # Relationships
    wishlist: Mapped["WishlistModel"] = relationship(
        "WishlistModel",
        back_populates="items",
    )
