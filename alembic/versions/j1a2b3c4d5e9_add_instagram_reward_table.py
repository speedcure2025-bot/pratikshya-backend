"""add_instagram_reward_table

Revision ID: j1a2b3c4d5e9
Revises: i1a2b3c4d5e8
Create Date: 2026-10-06 00:00:00.000000

Creates `commerce_instagram_reward` — tracks the full lifecycle of an Instagram
post reward submission:

  customer_id         FK → users.id            (CASCADE)   who posted
  order_id            FK → orders_order.id     (CASCADE)   the purchase they posted about
  instagram_username  VARCHAR(100)             customer's Instagram handle
  instagram_post_url  VARCHAR(500) nullable    link to the post
  status              VARCHAR(20)              PENDING | APPROVED | REJECTED
  verified_by         FK → users.id            (SET NULL)  staff who approved/rejected
  verified_at         TIMESTAMPTZ nullable     when verified
  rejection_reason    TEXT nullable            why rejected
  coupon_id           FK → commerce_coupon.id (SET NULL)  coupon generated on approval
  coupon_code         VARCHAR(100) nullable    denormalised for display

Indexes:
  ix_commerce_instagram_reward_id           (from Base)
  ix_commerce_instagram_reward_customer_id  lookup by customer
  ix_commerce_instagram_reward_order_id     lookup by order
  ix_commerce_instagram_reward_status       filter by status
  ix_commerce_instagram_reward_coupon_id    link back to coupon

Unique constraint:
  uq_instagram_reward_order_approved  one APPROVED reward per order
  (partial unique index — only enforces when status = 'APPROVED')

Foreign keys:
  fk_instagram_reward_customer_id   → users.id
  fk_instagram_reward_order_id      → orders_order.id
  fk_instagram_reward_verified_by   → users.id
  fk_instagram_reward_coupon_id     → commerce_coupon.id
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "j1a2b3c4d5e9"
down_revision: Union[str, None] = "i1a2b3c4d5e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Create commerce_instagram_reward ─────────────────────────────────────
    op.create_table(
        "commerce_instagram_reward",

        # ── Primary key (UUID string — matches Base convention) ───────────────
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),

        # ── Customer (who posted) ─────────────────────────────────────────────
        sa.Column("customer_id", sa.String(36), nullable=False),

        # ── Order (the purchase they posted about) ────────────────────────────
        sa.Column("order_id", sa.String(36), nullable=False),

        # ── Instagram identifiers ─────────────────────────────────────────────
        sa.Column("instagram_username", sa.String(100), nullable=False),
        sa.Column("instagram_post_url", sa.String(500), nullable=True),

        # ── Status (plain String — project convention, not a PG enum type) ────
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="PENDING",
        ),

        # ── Verification audit ────────────────────────────────────────────────
        sa.Column("verified_by", sa.String(36), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),

        # ── Generated coupon (NULL while PENDING or REJECTED) ─────────────────
        sa.Column("coupon_id", sa.String(36), nullable=True),
        sa.Column("coupon_code", sa.String(100), nullable=True),

        # ── Base timestamps ────────────────────────────────────────────────────
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),

        schema="pratikshya",
    )

    # ── Indexes ───────────────────────────────────────────────────────────────
    op.create_index(
        "ix_commerce_instagram_reward_id",
        "commerce_instagram_reward",
        ["id"],
        unique=False,
        schema="pratikshya",
    )
    op.create_index(
        "ix_commerce_instagram_reward_customer_id",
        "commerce_instagram_reward",
        ["customer_id"],
        unique=False,
        schema="pratikshya",
    )
    op.create_index(
        "ix_commerce_instagram_reward_order_id",
        "commerce_instagram_reward",
        ["order_id"],
        unique=False,
        schema="pratikshya",
    )
    op.create_index(
        "ix_commerce_instagram_reward_status",
        "commerce_instagram_reward",
        ["status"],
        unique=False,
        schema="pratikshya",
    )
    op.create_index(
        "ix_commerce_instagram_reward_coupon_id",
        "commerce_instagram_reward",
        ["coupon_id"],
        unique=False,
        schema="pratikshya",
    )

    # ── Partial unique index: one APPROVED reward per order ───────────────────
    # A partial index (WHERE status = 'APPROVED') enforces the business rule
    # "no order may receive more than one approved reward" at the DB level
    # without blocking multiple PENDING/REJECTED rows for the same order
    # (staff may reject a bad submission and approve a corrected one).
    op.execute(
        """
        CREATE UNIQUE INDEX uq_instagram_reward_order_approved
        ON pratikshya.commerce_instagram_reward (order_id)
        WHERE status = 'APPROVED'
        """
    )

    # ── Foreign keys ──────────────────────────────────────────────────────────
    # customer_id → users.id
    op.create_foreign_key(
        "fk_instagram_reward_customer_id",
        "commerce_instagram_reward", "users",
        ["customer_id"], ["id"],
        ondelete="CASCADE",
        source_schema="pratikshya",
        referent_schema="pratikshya",
    )
    # order_id → orders_order.id
    op.create_foreign_key(
        "fk_instagram_reward_order_id",
        "commerce_instagram_reward", "orders_order",
        ["order_id"], ["id"],
        ondelete="CASCADE",
        source_schema="pratikshya",
        referent_schema="pratikshya",
    )
    # verified_by → users.id  (SET NULL so deleting a staff user doesn't orphan records)
    op.create_foreign_key(
        "fk_instagram_reward_verified_by",
        "commerce_instagram_reward", "users",
        ["verified_by"], ["id"],
        ondelete="SET NULL",
        source_schema="pratikshya",
        referent_schema="pratikshya",
    )
    # coupon_id → commerce_coupon.id  (SET NULL — reward record survives coupon deletion)
    op.create_foreign_key(
        "fk_instagram_reward_coupon_id",
        "commerce_instagram_reward", "commerce_coupon",
        ["coupon_id"], ["id"],
        ondelete="SET NULL",
        source_schema="pratikshya",
        referent_schema="pratikshya",
    )


def downgrade() -> None:
    # Drop FKs first, then indexes, then table.
    op.drop_constraint(
        "fk_instagram_reward_coupon_id",
        "commerce_instagram_reward",
        type_="foreignkey",
        schema="pratikshya",
    )
    op.drop_constraint(
        "fk_instagram_reward_verified_by",
        "commerce_instagram_reward",
        type_="foreignkey",
        schema="pratikshya",
    )
    op.drop_constraint(
        "fk_instagram_reward_order_id",
        "commerce_instagram_reward",
        type_="foreignkey",
        schema="pratikshya",
    )
    op.drop_constraint(
        "fk_instagram_reward_customer_id",
        "commerce_instagram_reward",
        type_="foreignkey",
        schema="pratikshya",
    )

    # Drop partial unique index (raw SQL — Alembic has no API for partial indexes)
    op.execute(
        "DROP INDEX IF EXISTS pratikshya.uq_instagram_reward_order_approved"
    )

    op.drop_index(
        "ix_commerce_instagram_reward_coupon_id",
        table_name="commerce_instagram_reward",
        schema="pratikshya",
    )
    op.drop_index(
        "ix_commerce_instagram_reward_status",
        table_name="commerce_instagram_reward",
        schema="pratikshya",
    )
    op.drop_index(
        "ix_commerce_instagram_reward_order_id",
        table_name="commerce_instagram_reward",
        schema="pratikshya",
    )
    op.drop_index(
        "ix_commerce_instagram_reward_customer_id",
        table_name="commerce_instagram_reward",
        schema="pratikshya",
    )
    op.drop_index(
        "ix_commerce_instagram_reward_id",
        table_name="commerce_instagram_reward",
        schema="pratikshya",
    )

    op.drop_table("commerce_instagram_reward", schema="pratikshya")
