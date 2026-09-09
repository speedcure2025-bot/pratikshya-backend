"""Durable media asset — SQLAlchemy model.

One row per verified object in the configured store (`STORAGE_PROVIDER`).
The table is created by the Alembic revision
``b6b5dcfb675b_add_media_asset_and_product_media_tables``; this class mirrors
that schema exactly (same columns, same nullability, same constraints) so the
ORM never describes a shape PostgreSQL does not actually enforce.

Index/constraint notes (the migration is the authority):

  · ``object_key`` is UNIQUE via the named constraint ``uq_media_asset_object_key``
    — registration looks an asset up by key and must never create a second row
    for the same object. Declared as a ``UniqueConstraint`` (not
    ``unique=True``) so the constraint name in code and in PostgreSQL match.
  · ``checksum_sha256`` keeps ``index=True`` for duplicate-byte detection.
  · ``status`` has no index: no query in the application filters on it, and the
    project does not carry speculative indexes.
  · ``uploaded_by`` is nullable audit metadata. It references ``users.id`` with
    ``ON DELETE SET NULL``, matching how the rest of the schema treats a
    nullable user reference (``orders_order.user_id``,
    ``employee_performance.reviewer_id``): removing a user must neither destroy
    the asset record nor block the delete.
"""

from typing import Optional

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MediaAssetModel(Base):
    """Durable database identity for an object in the configured store."""

    __tablename__ = "media_media_asset"

    # ── Object identity ──────────────────────────────────────────────────────
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_provider: Mapped[str] = mapped_column(
        String(20), nullable=False, default="local"
    )
    media_type: Mapped[str] = mapped_column(String(30), nullable=False, default="image")
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # ── Optional descriptive metadata ────────────────────────────────────────
    width: Mapped[Optional[int]] = mapped_column(Integer)
    height: Mapped[Optional[int]] = mapped_column(Integer)
    title: Mapped[Optional[str]] = mapped_column(String(255))
    alt_text: Mapped[Optional[str]] = mapped_column(Text)
    caption: Mapped[Optional[str]] = mapped_column(Text)

    # ── Lifecycle ────────────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="uploaded")
    scope: Mapped[str] = mapped_column(String(30), nullable=False, default="product")
    uploaded_by: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )

    __table_args__ = (
        UniqueConstraint("object_key", name="uq_media_asset_object_key"),
    )
