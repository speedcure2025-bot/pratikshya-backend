"""add wishlist columns and activity log columns

Revision ID: z1a2b3c4d5e6
Revises: f1a2b3c4d5e6
Create Date: 2026-08-23 10:00:00.000000

Adds:
  - commerce_wishlist.customer_id column + unique constraint
  - commerce_wishlist_item columns (wishlist_id, product_id) + unique constraint
  - audit_activity_log columns for the shared diary
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "z1a2b3c4d5e6"
down_revision: Union[str, None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── commerce_wishlist ──────────────────────────────────────────────────────
    # Add customer_id if missing (table was created as a stub in initial_schema)
    op.add_column(
        "commerce_wishlist",
        sa.Column("customer_id", sa.String(length=36), nullable=True),
    )
    # Make it non-null and unique once populated
    with op.batch_alter_table("commerce_wishlist") as batch_op:
        batch_op.create_index("ix_commerce_wishlist_customer_id", ["customer_id"], unique=True)

    # ── commerce_wishlist_item ─────────────────────────────────────────────────
    op.add_column(
        "commerce_wishlist_item",
        sa.Column("wishlist_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "commerce_wishlist_item",
        sa.Column("product_id", sa.String(length=100), nullable=True),
    )
    with op.batch_alter_table("commerce_wishlist_item") as batch_op:
        batch_op.create_index("ix_commerce_wishlist_item_wishlist_id", ["wishlist_id"])
        batch_op.create_index("ix_commerce_wishlist_item_product_id", ["product_id"])
        batch_op.create_unique_constraint(
            "uq_wishlist_product", ["wishlist_id", "product_id"]
        )

    # ── audit_activity_log ─────────────────────────────────────────────────────
    # Add all the diary columns (table was a stub with only id + timestamps)
    for col_name, col_type, nullable in [
        ("actor_employee_id",   sa.String(36),  True),
        ("actor_name",          sa.String(255), True),
        ("target_employee_id",  sa.String(36),  True),
        ("target_product_id",   sa.String(100), True),
        ("target_offer_id",     sa.String(36),  True),
        ("target_category_id",  sa.String(36),  True),
        ("target_collection_id",sa.String(36),  True),
        ("target_order_id",     sa.String(36),  True),
        ("target_return_id",    sa.String(36),  True),
        ("target_media_id",     sa.String(36),  True),
        ("action",              sa.String(100), True),
        ("summary",             sa.Text(),      True),
    ]:
        op.add_column(
            "audit_activity_log",
            sa.Column(col_name, col_type, nullable=nullable),
        )

    # Indexes on commonly-queried columns
    op.create_index("ix_activity_log_actor_employee_id",   "audit_activity_log", ["actor_employee_id"])
    op.create_index("ix_activity_log_target_employee_id",  "audit_activity_log", ["target_employee_id"])
    op.create_index("ix_activity_log_target_product_id",   "audit_activity_log", ["target_product_id"])
    op.create_index("ix_activity_log_action",              "audit_activity_log", ["action"])


def downgrade() -> None:
    # Drop wishlist item columns
    with op.batch_alter_table("commerce_wishlist_item") as batch_op:
        batch_op.drop_constraint("uq_wishlist_product", type_="unique")
        batch_op.drop_index("ix_commerce_wishlist_item_product_id")
        batch_op.drop_index("ix_commerce_wishlist_item_wishlist_id")
    op.drop_column("commerce_wishlist_item", "product_id")
    op.drop_column("commerce_wishlist_item", "wishlist_id")

    # Drop wishlist columns
    with op.batch_alter_table("commerce_wishlist") as batch_op:
        batch_op.drop_index("ix_commerce_wishlist_customer_id")
    op.drop_column("commerce_wishlist", "customer_id")

    # Drop activity log columns
    op.drop_index("ix_activity_log_action",             "audit_activity_log")
    op.drop_index("ix_activity_log_target_product_id",  "audit_activity_log")
    op.drop_index("ix_activity_log_target_employee_id", "audit_activity_log")
    op.drop_index("ix_activity_log_actor_employee_id",  "audit_activity_log")
    for col in [
        "summary", "action", "target_media_id", "target_return_id",
        "target_order_id", "target_collection_id", "target_category_id",
        "target_offer_id", "target_product_id", "target_employee_id",
        "actor_name", "actor_employee_id",
    ]:
        op.drop_column("audit_activity_log", col)
