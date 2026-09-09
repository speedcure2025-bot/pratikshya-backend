"""add_cart_coupon_columns

Adds all required columns to commerce_cart, commerce_cart_item,
commerce_coupon, and commerce_coupon_redemption tables to match the
models defined in app/models/commerce/.

Revision ID: d1e2f3a4b5c6
Revises: 597f883749d8
Create Date: 2026-08-20 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, None] = "c9d1e2f3a4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── commerce_coupon — add all business columns ────────────────────────────
    op.add_column("commerce_coupon", sa.Column("code", sa.String(100), nullable=True))
    op.add_column("commerce_coupon", sa.Column("name", sa.String(255), nullable=True))
    op.add_column("commerce_coupon", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("commerce_coupon", sa.Column("discount_type", sa.String(20), nullable=False, server_default="percentage"))
    op.add_column("commerce_coupon", sa.Column("discount_value", sa.Float(), nullable=False, server_default="0"))
    op.add_column("commerce_coupon", sa.Column("minimum_order_value", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("commerce_coupon", sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("commerce_coupon", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("commerce_coupon", sa.Column("usage_limit", sa.Integer(), nullable=True))
    op.add_column("commerce_coupon", sa.Column("usage_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("commerce_coupon", sa.Column("per_customer_limit", sa.Integer(), nullable=True))
    op.add_column("commerce_coupon", sa.Column("eligible_customer_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("commerce_coupon", sa.Column("eligible_product_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("commerce_coupon", sa.Column("eligible_category_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("commerce_coupon", sa.Column("eligible_collection_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("commerce_coupon", sa.Column("excluded_product_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("commerce_coupon", sa.Column("excluded_category_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("commerce_coupon", sa.Column("is_stackable", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("commerce_coupon", sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("commerce_coupon", sa.Column("created_by", sa.String(36), nullable=True))
    op.create_index("ix_commerce_coupon_code", "commerce_coupon", ["code"], unique=True)

    # ── commerce_coupon_redemption — add redemption columns ───────────────────
    op.add_column("commerce_coupon_redemption", sa.Column(
        "coupon_id", sa.String(36), nullable=True,
    ))
    op.add_column("commerce_coupon_redemption", sa.Column("customer_id", sa.String(36), nullable=True))
    op.add_column("commerce_coupon_redemption", sa.Column("order_id", sa.String(36), nullable=True))
    op.add_column("commerce_coupon_redemption", sa.Column("coupon_code", sa.String(100), nullable=True))
    op.add_column("commerce_coupon_redemption", sa.Column("discount_amount", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("ix_commerce_coupon_redemption_coupon_id", "commerce_coupon_redemption", ["coupon_id"])
    op.create_index("ix_commerce_coupon_redemption_customer_id", "commerce_coupon_redemption", ["customer_id"])
    op.create_index("ix_commerce_coupon_redemption_order_id", "commerce_coupon_redemption", ["order_id"])
    # FK from coupon_redemption → coupon (nullable FK so old rows survive)
    op.create_foreign_key(
        "fk_coupon_redemption_coupon_id",
        "commerce_coupon_redemption", "commerce_coupon",
        ["coupon_id"], ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_coupon_redemption_customer_id",
        "commerce_coupon_redemption", "users",
        ["customer_id"], ["id"],
        ondelete="CASCADE",
    )

    # ── commerce_cart — add ownership + coupon columns ────────────────────────
    op.add_column("commerce_cart", sa.Column("customer_id", sa.String(36), nullable=True))
    op.add_column("commerce_cart", sa.Column("coupon_code", sa.String(100), nullable=True))
    op.add_column("commerce_cart", sa.Column("coupon_id", sa.String(36), nullable=True))
    op.add_column("commerce_cart", sa.Column("coupon_lapsed", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("commerce_cart", sa.Column("customer_note", sa.Text(), nullable=True))
    op.create_index("ix_commerce_cart_customer_id", "commerce_cart", ["customer_id"], unique=True)
    op.create_foreign_key(
        "fk_cart_customer_id",
        "commerce_cart", "users",
        ["customer_id"], ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_cart_coupon_id",
        "commerce_cart", "commerce_coupon",
        ["coupon_id"], ["id"],
        ondelete="SET NULL",
    )

    # ── commerce_cart_item — add line columns ─────────────────────────────────
    op.add_column("commerce_cart_item", sa.Column("cart_id", sa.String(36), nullable=True))
    op.add_column("commerce_cart_item", sa.Column("product_id", sa.String(36), nullable=True))
    op.add_column("commerce_cart_item", sa.Column("color", sa.String(100), nullable=True))
    op.add_column("commerce_cart_item", sa.Column("size", sa.String(50), nullable=True))
    op.add_column("commerce_cart_item", sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("commerce_cart_item", sa.Column(
        "added_at",
        sa.DateTime(timezone=True),
        nullable=True,
        server_default=sa.func.now(),
    ))
    op.create_index("ix_commerce_cart_item_cart_id", "commerce_cart_item", ["cart_id"])
    op.create_index("ix_commerce_cart_item_product_id", "commerce_cart_item", ["product_id"])
    op.create_unique_constraint(
        "uq_cart_item_line",
        "commerce_cart_item",
        ["cart_id", "product_id", "color", "size"],
    )
    op.create_foreign_key(
        "fk_cart_item_cart_id",
        "commerce_cart_item", "commerce_cart",
        ["cart_id"], ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    # ── commerce_cart_item ────────────────────────────────────────────────────
    op.drop_constraint("fk_cart_item_cart_id", "commerce_cart_item", type_="foreignkey")
    op.drop_constraint("uq_cart_item_line", "commerce_cart_item", type_="unique")
    op.drop_index("ix_commerce_cart_item_product_id", table_name="commerce_cart_item")
    op.drop_index("ix_commerce_cart_item_cart_id", table_name="commerce_cart_item")
    for col in ["added_at", "quantity", "size", "color", "product_id", "cart_id"]:
        op.drop_column("commerce_cart_item", col)

    # ── commerce_cart ─────────────────────────────────────────────────────────
    op.drop_constraint("fk_cart_coupon_id", "commerce_cart", type_="foreignkey")
    op.drop_constraint("fk_cart_customer_id", "commerce_cart", type_="foreignkey")
    op.drop_index("ix_commerce_cart_customer_id", table_name="commerce_cart")
    for col in ["customer_note", "coupon_lapsed", "coupon_id", "coupon_code", "customer_id"]:
        op.drop_column("commerce_cart", col)

    # ── commerce_coupon_redemption ────────────────────────────────────────────
    op.drop_constraint("fk_coupon_redemption_customer_id", "commerce_coupon_redemption", type_="foreignkey")
    op.drop_constraint("fk_coupon_redemption_coupon_id", "commerce_coupon_redemption", type_="foreignkey")
    op.drop_index("ix_commerce_coupon_redemption_order_id", table_name="commerce_coupon_redemption")
    op.drop_index("ix_commerce_coupon_redemption_customer_id", table_name="commerce_coupon_redemption")
    op.drop_index("ix_commerce_coupon_redemption_coupon_id", table_name="commerce_coupon_redemption")
    for col in ["discount_amount", "coupon_code", "order_id", "customer_id", "coupon_id"]:
        op.drop_column("commerce_coupon_redemption", col)

    # ── commerce_coupon ───────────────────────────────────────────────────────
    op.drop_index("ix_commerce_coupon_code", table_name="commerce_coupon")
    for col in [
        "created_by", "is_active", "is_stackable", "excluded_category_ids",
        "excluded_product_ids", "eligible_collection_ids", "eligible_category_ids",
        "eligible_product_ids", "eligible_customer_ids", "per_customer_limit",
        "usage_count", "usage_limit", "expires_at", "starts_at",
        "minimum_order_value", "discount_value", "discount_type",
        "description", "name", "code",
    ]:
        op.drop_column("commerce_coupon", col)
