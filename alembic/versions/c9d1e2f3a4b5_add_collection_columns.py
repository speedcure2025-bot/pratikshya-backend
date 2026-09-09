"""add_collection_columns

Adds all required columns to the existing catalog_collection table to
match the CollectionModel defined in app/models/catalog/collection.py.

Revision ID: c9d1e2f3a4b5
Revises: a1b2c3d4e5f6
Create Date: 2026-08-14 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c9d1e2f3a4b5"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Identity ──────────────────────────────────────────────────────────────
    op.add_column(
        "catalog_collection",
        sa.Column("name", sa.String(length=200), nullable=False, server_default=""),
    )
    op.add_column(
        "catalog_collection",
        sa.Column("slug", sa.String(length=220), nullable=False, server_default=""),
    )
    op.create_unique_constraint(
        "uq_catalog_collection_slug", "catalog_collection", ["slug"]
    )
    op.create_index(
        "ix_catalog_collection_slug", "catalog_collection", ["slug"], unique=False
    )

    # ── Display ───────────────────────────────────────────────────────────────
    op.add_column(
        "catalog_collection",
        sa.Column("eyebrow", sa.String(length=120), nullable=True, server_default=""),
    )
    op.add_column(
        "catalog_collection",
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.add_column(
        "catalog_collection",
        sa.Column("image", sa.Text(), nullable=True),
    )
    op.add_column(
        "catalog_collection",
        sa.Column("hero_media_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "catalog_collection",
        sa.Column("thumbnail_media_id", sa.String(length=64), nullable=True),
    )

    # ── Classification ────────────────────────────────────────────────────────
    op.add_column(
        "catalog_collection",
        sa.Column(
            "type", sa.String(length=30), nullable=False, server_default="MANUAL"
        ),
    )
    op.create_index(
        "ix_catalog_collection_type", "catalog_collection", ["type"], unique=False
    )

    # ── Scheduling ────────────────────────────────────────────────────────────
    op.add_column(
        "catalog_collection",
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "catalog_collection",
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=True),
    )

    # ── Status ────────────────────────────────────────────────────────────────
    op.add_column(
        "catalog_collection",
        sa.Column(
            "status", sa.String(length=30), nullable=False, server_default="DRAFT"
        ),
    )
    op.create_index(
        "ix_catalog_collection_status", "catalog_collection", ["status"], unique=False
    )

    # ── Merchandising ─────────────────────────────────────────────────────────
    op.add_column(
        "catalog_collection",
        sa.Column(
            "featured", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "catalog_collection",
        sa.Column(
            "sort_order", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.create_index(
        "ix_catalog_collection_sort_order",
        "catalog_collection",
        ["sort_order"],
        unique=False,
    )

    # ── Membership ────────────────────────────────────────────────────────────
    op.add_column(
        "catalog_collection",
        sa.Column(
            "explicit_product_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            server_default="[]",
        ),
    )
    op.add_column(
        "catalog_collection",
        sa.Column(
            "rule",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            server_default="{}",
        ),
    )

    # ── Audit ─────────────────────────────────────────────────────────────────
    op.add_column(
        "catalog_collection",
        sa.Column("created_by", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "catalog_collection",
        sa.Column("updated_by", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("catalog_collection", "updated_by")
    op.drop_column("catalog_collection", "created_by")
    op.drop_column("catalog_collection", "rule")
    op.drop_column("catalog_collection", "explicit_product_ids")
    op.drop_index("ix_catalog_collection_sort_order", table_name="catalog_collection")
    op.drop_column("catalog_collection", "sort_order")
    op.drop_column("catalog_collection", "featured")
    op.drop_index("ix_catalog_collection_status", table_name="catalog_collection")
    op.drop_column("catalog_collection", "status")
    op.drop_column("catalog_collection", "end_date")
    op.drop_column("catalog_collection", "start_date")
    op.drop_index("ix_catalog_collection_type", table_name="catalog_collection")
    op.drop_column("catalog_collection", "type")
    op.drop_column("catalog_collection", "thumbnail_media_id")
    op.drop_column("catalog_collection", "hero_media_id")
    op.drop_column("catalog_collection", "image")
    op.drop_column("catalog_collection", "description")
    op.drop_column("catalog_collection", "eyebrow")
    op.drop_index("ix_catalog_collection_slug", table_name="catalog_collection")
    op.drop_constraint(
        "uq_catalog_collection_slug", "catalog_collection", type_="unique"
    )
    op.drop_column("catalog_collection", "slug")
    op.drop_column("catalog_collection", "name")
