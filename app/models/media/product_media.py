"""Product ↔ media mapping — SQLAlchemy model.

The explicit, ordered association between a catalogue product and a registered
: class:`~app.models.media.media_asset.MediaAssetModel`. This table is the
source of truth for NEW product media; legacy product JSON columns stay
dual-read for catalogue data that predates it.

The table is created by the Alembic revision
``b6b5dcfb675b_add_media_asset_and_product_media_tables``. PostgreSQL — not
this class — enforces the referential rules:

  · ``product_id`` → ``catalog_product.id``   NOT NULL, ON DELETE CASCADE
  · ``media_id``   → ``media_media_asset.id`` NOT NULL, ON DELETE CASCADE
  · ``UNIQUE (product_id, media_id)`` as ``uq_product_media_asset``, so the same
    asset can never be mapped to the same product twice.

CASCADE on both sides is the project's existing rule for a NOT NULL reference
whose row is meaningless without its parent (``commerce_cart_item.cart_id``,
``orders_order_item.order_id``, ``role_permissions.*``). SET NULL is reserved
for nullable references, which neither of these is.

Indexing note: ``product_id`` deliberately has no index of its own. It is the
leftmost column of ``uq_product_media_asset``, so PostgreSQL already uses that
unique index for "media of product P" and for the product-side FK cascade; a
second single-column index would be pure duplication. ``media_id`` is not
covered by that index, so it keeps ``index=True``.

``assigned_by`` stays a plain user id without a foreign key, matching the other
"who did it" audit columns in this schema (``catalog_product.created_by``,
``admin_setting.updated_by``).

No ``relationship()`` is declared: every read of this table goes through the
explicit joins in ``app/services/media/product_media_records.py``, and an
unused lazy relationship would only add a synchronous-load hazard under the
async session.
"""

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ProductMediaModel(Base):
    """Explicit ordered product-to-media mapping (source of truth for new media)."""

    __tablename__ = "media_product_media"

    # ── Both ends of the mapping are mandatory ───────────────────────────────
    product_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("catalog_product.id", ondelete="CASCADE"),
        nullable=False,
    )
    media_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("media_media_asset.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Placement within the product's gallery ───────────────────────────────
    role: Mapped[str] = mapped_column(String(30), nullable=False, default="gallery")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── Assignment audit ─────────────────────────────────────────────────────
    assigned_by: Mapped[str | None] = mapped_column(String(36))
    assignment_note: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        UniqueConstraint("product_id", "media_id", name="uq_product_media_asset"),
    )
