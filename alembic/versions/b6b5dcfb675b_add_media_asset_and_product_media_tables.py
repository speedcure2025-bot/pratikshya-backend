"""add_media_asset_and_product_media_tables

Revision ID: b6b5dcfb675b
Revises: a2b3c4d5e6f7
Create Date: 2026-08-27 00:00:00.000000

Creates the durable media schema in the ``pratikshya`` PostgreSQL schema:

    media_media_asset     one row per verified object in the configured store
    media_product_media   the ordered product <-> media-asset mapping

Until this revision both tables existed only as the column-less stubs that
``8f0223843258_initial_schema`` emitted for every mapped model
(``id`` / ``created_at`` / ``updated_at`` plus an ``id`` index), which
``m001schema`` then moved from ``public`` into ``pratikshya``. A stub cannot
hold a media record — there was nowhere to put an object key — so no
application data can exist in either table. This revision therefore replaces
them with the real tables rather than layering columns onto empty shells.
``media_marketing_media`` and ``media_media_review`` are separate concerns and
are deliberately left untouched here.

Relational guarantees created by this revision (enforced by PostgreSQL
itself, not merely declared on the ORM classes):

    media_media_asset.id                                     PRIMARY KEY
    media_media_asset.object_key                             UNIQUE
                                                             uq_media_asset_object_key
    media_media_asset.uploaded_by    -> users.id             ON DELETE SET NULL
    media_product_media.id                                   PRIMARY KEY
    media_product_media.product_id   -> catalog_product.id   ON DELETE CASCADE
    media_product_media.media_id     -> media_media_asset.id ON DELETE CASCADE
    media_product_media(product_id, media_id)                UNIQUE
                                                             uq_product_media_asset

ON DELETE behaviour follows the convention already established across this
schema rather than a new one:

  * a NOT NULL reference whose row has no meaning without its parent
    cascades — ``commerce_cart_item.cart_id``, ``orders_order_item.order_id``,
    ``commerce_wishlist_item.wishlist_id``, ``role_permissions.role_id``;
  * a NULLABLE reference to an entity that may outlive the row is set to
    null — ``commerce_cart.coupon_id``, ``employee_performance.reviewer_id``,
    ``orders_order.user_id``.

Both mapping columns are NOT NULL, so both cascade; ``uploaded_by`` is
nullable audit metadata, so removing a user nulls it instead of destroying the
asset record (or blocking the delete).

Indexes are limited to the access patterns the application actually executes:

  * ``uq_media_asset_object_key`` — every registration looks the asset up by
    object key and must not create a second row for the same object.
  * ``ix_media_media_asset_checksum_sha256`` — duplicate-byte detection when
    the same file is uploaded under a different key.
  * ``uq_product_media_asset`` — its leftmost column is ``product_id``, so the
    same index serves "media of product P" and the product-side FK cascade.
    A separate single-column index on ``product_id`` would be redundant and is
    therefore not created.
  * ``ix_media_product_media_media_id`` — the asset -> product direction
    (the read-model join and the asset-side FK cascade) has no other index.

``ix_media_media_asset_id`` / ``ix_media_product_media_id`` are recreated
because ``Base`` declares ``id`` with ``index=True`` for every table in this
project; they existed on the stubs and the ORM contract expects them.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b6b5dcfb675b"
down_revision: Union[str, None] = "a2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Every application table lives in this schema since ``m001schema``.
SCHEMA = "pratikshya"


def upgrade() -> None:
    # ── Replace the empty stubs (child first, parent second) ──────────────────
    op.drop_table("media_product_media", schema=SCHEMA)
    op.drop_table("media_media_asset", schema=SCHEMA)

    # ── media_media_asset: durable identity for one stored object ─────────────
    op.create_table(
        "media_media_asset",

        # Base identity + audit timestamps (values supplied by the ORM)
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),

        # ── Object identity ───────────────────────────────────────────────────
        sa.Column(
            "object_key",
            sa.String(length=512),
            nullable=False,
            comment="Key of the object in the configured store, e.g. products/PF-001/a.avif.",
        ),
        sa.Column(
            "storage_provider",
            sa.String(length=20),
            nullable=False,
            server_default="local",
            comment="Provider that holds the bytes: 'local' or 's3'.",
        ),
        sa.Column(
            "media_type",
            sa.String(length=30),
            nullable=False,
            server_default="image",
        ),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False, comment="Bytes."),
        sa.Column(
            "checksum_sha256",
            sa.String(length=64),
            nullable=False,
            comment="SHA-256 of the stored bytes; used for duplicate detection.",
        ),

        # ── Optional descriptive metadata ─────────────────────────────────────
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("alt_text", sa.Text(), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),

        # ── Lifecycle ─────────────────────────────────────────────────────────
        sa.Column(
            "status",
            sa.String(length=30),
            nullable=False,
            server_default="uploaded",
        ),
        sa.Column(
            "scope",
            sa.String(length=30),
            nullable=False,
            server_default="product",
            comment="'product' or 'marketing'.",
        ),
        sa.Column(
            "uploaded_by",
            sa.String(length=36),
            nullable=True,
            comment="User id of the uploader; nulled if that user is removed.",
        ),

        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["uploaded_by"],
            [f"{SCHEMA}.users.id"],
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("object_key", name="uq_media_asset_object_key"),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_media_media_asset_id"),
        "media_media_asset",
        ["id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_media_media_asset_checksum_sha256",
        "media_media_asset",
        ["checksum_sha256"],
        unique=False,
        schema=SCHEMA,
    )

    # ── media_product_media: ordered product <-> asset mapping ────────────────
    op.create_table(
        "media_product_media",

        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),

        # ── Both ends of the mapping are mandatory ────────────────────────────
        sa.Column("product_id", sa.String(length=36), nullable=False),
        sa.Column("media_id", sa.String(length=36), nullable=False),

        # ── Placement within the product's gallery ────────────────────────────
        sa.Column(
            "role",
            sa.String(length=30),
            nullable=False,
            server_default="gallery",
            comment="'COVER' marks the primary image; anything else is gallery.",
        ),
        sa.Column(
            "sort_order",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "is_primary",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),

        # ── Assignment audit (plain user id, like catalog_product.created_by) ──
        sa.Column("assigned_by", sa.String(length=36), nullable=True),
        sa.Column("assignment_note", sa.String(length=500), nullable=True),

        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["product_id"],
            [f"{SCHEMA}.catalog_product.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["media_id"],
            [f"{SCHEMA}.media_media_asset.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "product_id",
            "media_id",
            name="uq_product_media_asset",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_media_product_media_id"),
        "media_product_media",
        ["id"],
        unique=False,
        schema=SCHEMA,
    )
    # media_id is not the leftmost column of uq_product_media_asset, so the
    # asset -> product direction (read-model join, asset-side FK cascade) needs
    # an index of its own.
    op.create_index(
        "ix_media_product_media_media_id",
        "media_product_media",
        ["media_id"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    # Drop the real tables (indexes owned by a table go with it, but the named
    # constraints/indexes are removed explicitly so the statements are legible).
    op.drop_index(
        "ix_media_product_media_media_id",
        table_name="media_product_media",
        schema=SCHEMA,
    )
    op.drop_constraint(
        "uq_product_media_asset", "media_product_media", schema=SCHEMA, type_="unique"
    )
    op.drop_index(
        "ix_media_product_media_id", table_name="media_product_media", schema=SCHEMA
    )
    op.drop_table("media_product_media", schema=SCHEMA)

    op.drop_index(
        "ix_media_media_asset_checksum_sha256",
        table_name="media_media_asset",
        schema=SCHEMA,
    )
    op.drop_constraint(
        "uq_media_asset_object_key", "media_media_asset", schema=SCHEMA, type_="unique"
    )
    op.drop_index(
        "ix_media_media_asset_id", table_name="media_media_asset", schema=SCHEMA
    )
    op.drop_table("media_media_asset", schema=SCHEMA)

    # Restore the exact stub shape emitted by 8f0223843258_initial_schema.
    op.create_table(
        "media_media_asset",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_media_media_asset_id"),
        "media_media_asset",
        ["id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_table(
        "media_product_media",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_media_product_media_id"),
        "media_product_media",
        ["id"],
        unique=False,
        schema=SCHEMA,
    )
