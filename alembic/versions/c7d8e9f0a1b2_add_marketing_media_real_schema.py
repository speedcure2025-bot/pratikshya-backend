"""
add_marketing_media_real_schema

Revision ID: c7d8e9f0a1b2
Revises: m001schema
Create Date: 2026-09-09

Replaces the empty placeholder `media_marketing_media` (only id/created_at/
updated_at from Base) with the production-ready marketing media table.

Until this revision the table could not hold any business data — there was
nowhere to put a placement, object key, or sort order — so no application
data can exist in it. This revision therefore drops the stub and recreates
the real table, matching the pattern used for `media_media_asset` and
`media_product_media` in b6b5dcfb675b.

New schema (pratikshya.media_marketing_media):

  id               UUID PK (Base)
  created_at       TIMESTAMPTZ (Base)
  updated_at       TIMESTAMPTZ (Base)
  placement        VARCHAR(50) NOT NULL DEFAULT 'HOME_HERO' INDEX
  object_key       VARCHAR(512) NOT NULL
  media_asset_id   FK → media_media_asset.id ON DELETE SET NULL NULLABLE INDEX
  title            VARCHAR(255) NULLABLE
  subtitle         VARCHAR(500) NULLABLE
  cta_label        VARCHAR(100) NULLABLE
  cta_href         VARCHAR(500) NULLABLE
  alt_text         TEXT NULLABLE
  sort_order       INTEGER NOT NULL DEFAULT 0
  is_active        BOOLEAN NOT NULL DEFAULT TRUE
  created_by       FK → users.id ON DELETE SET NULL NULLABLE
  updated_by       FK → users.id ON DELETE SET NULL NULLABLE

Constraints:
  uq_marketing_media_placement_object_key UNIQUE (placement, object_key)
  ix_marketing_media_placement_active_sort (placement, is_active, sort_order)
  ix_marketing_media_placement_sort (placement, sort_order)

ON DELETE behaviour follows existing convention:
  * media_asset_id, created_by, updated_by are nullable audit/reference
    fields → SET NULL (matches media_media_asset.uploaded_by, orders.user_id)

This table is the source of truth for HOME_HERO and other marketing
placements. PRODUCT MEDIA (products/...) and COLLECTION/EDITORIAL
(collections/...) remain separate.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, None] = "m001schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "pratikshya"
TABLE = "media_marketing_media"


def upgrade() -> None:
    # Drop the empty stub — no business data can exist in it.
    op.drop_table(TABLE, schema=SCHEMA)

    # Recreate with real columns.
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),

        sa.Column("placement", sa.String(length=50), nullable=False, server_default="HOME_HERO"),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("media_asset_id", sa.String(length=36), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("subtitle", sa.String(length=500), nullable=True),
        sa.Column("cta_label", sa.String(length=100), nullable=True),
        sa.Column("cta_href", sa.String(length=500), nullable=True),
        sa.Column("alt_text", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("updated_by", sa.String(length=36), nullable=True),

        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["media_asset_id"], [f"{SCHEMA}.media_media_asset.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], [f"{SCHEMA}.users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], [f"{SCHEMA}.users.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint(
            "placement", "object_key", name="uq_marketing_media_placement_object_key"
        ),
        schema=SCHEMA,
    )

    op.create_index(
        "ix_media_marketing_media_id", TABLE, ["id"], unique=False, schema=SCHEMA
    )
    op.create_index(
        "ix_media_marketing_media_placement",
        TABLE,
        ["placement"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_media_marketing_media_media_asset_id",
        TABLE,
        ["media_asset_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_marketing_media_placement_active_sort",
        TABLE,
        ["placement", "is_active", "sort_order"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_marketing_media_placement_sort",
        TABLE,
        ["placement", "sort_order"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table(TABLE, schema=SCHEMA)

    # Recreate the original empty stub (Base columns only) so downgrade is
    # reversible to the state before this revision.
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_media_marketing_media_id", TABLE, ["id"], unique=False, schema=SCHEMA
    )
