"""add_category_subcategory_columns

Revision ID: a1b2c3d4e5f6
Revises: 597f883749d8
Create Date: 2026-08-14 00:00:00.000000

Adds all fields to catalog_category (was a skeleton with id + timestamps only)
and creates the new catalog_subcategory table.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "597f883749d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── catalog_category: add missing columns ─────────────────────────────────
    op.add_column(
        "catalog_category",
        sa.Column("name", sa.String(length=100), nullable=False, server_default=""),
    )
    op.add_column(
        "catalog_category",
        sa.Column("slug", sa.String(length=120), nullable=False, server_default=""),
    )
    op.add_column(
        "catalog_category",
        sa.Column("eyebrow", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "catalog_category",
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.add_column(
        "catalog_category",
        sa.Column("image", sa.Text(), nullable=True),
    )
    op.add_column(
        "catalog_category",
        sa.Column("banner_media_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "catalog_category",
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "catalog_category",
        sa.Column("featured", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "catalog_category",
        sa.Column("seo_title", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "catalog_category",
        sa.Column("seo_description", sa.Text(), nullable=True),
    )
    op.add_column(
        "catalog_category",
        sa.Column("status", sa.String(length=30), nullable=False, server_default="DRAFT"),
    )
    op.add_column(
        "catalog_category",
        sa.Column("created_by", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "catalog_category",
        sa.Column("updated_by", sa.String(length=64), nullable=True),
    )

    # Unique constraint on slug
    op.create_unique_constraint("uq_catalog_category_slug", "catalog_category", ["slug"])

    # Indexes
    op.create_index("ix_catalog_category_status", "catalog_category", ["status"])
    op.create_index("ix_catalog_category_sort_order", "catalog_category", ["sort_order"])
    op.create_index("ix_catalog_category_slug_idx", "catalog_category", ["slug"])

    # ── catalog_subcategory: create table ─────────────────────────────────────
    op.create_table(
        "catalog_subcategory",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("category_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("image", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="DRAFT"),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["catalog_category.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_catalog_subcategory_id"), "catalog_subcategory", ["id"], unique=False
    )
    op.create_index(
        "ix_catalog_subcategory_category_id",
        "catalog_subcategory",
        ["category_id"],
    )
    op.create_index(
        "ix_catalog_subcategory_slug",
        "catalog_subcategory",
        ["slug"],
    )
    op.create_index(
        "ix_catalog_subcategory_status",
        "catalog_subcategory",
        ["status"],
    )
    # Unique: slug within a category
    op.create_index(
        "uq_subcategory_category_slug",
        "catalog_subcategory",
        ["category_id", "slug"],
        unique=True,
    )


def downgrade() -> None:
    # Drop subcategory table
    op.drop_table("catalog_subcategory")

    # Drop indexes added to category
    op.drop_index("ix_catalog_category_slug_idx", table_name="catalog_category")
    op.drop_index("ix_catalog_category_sort_order", table_name="catalog_category")
    op.drop_index("ix_catalog_category_status", table_name="catalog_category")
    op.drop_constraint("uq_catalog_category_slug", "catalog_category", type_="unique")

    # Drop columns added to category
    for col in [
        "name", "slug", "eyebrow", "description", "image", "banner_media_id",
        "sort_order", "featured", "seo_title", "seo_description",
        "status", "created_by", "updated_by",
    ]:
        op.drop_column("catalog_category", col)
