"""Customer behavioral interactions; operational audit diary unchanged.

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
"""
from alembic import op
import sqlalchemy as sa

revision = "d8e9f0a1b2c3"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_product_interactions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("customer_id", sa.String(36), nullable=False),
        sa.Column("product_id", sa.String(36), nullable=False),
        sa.Column("event_type", sa.String(20), nullable=False),
        sa.Column("dedup_key", sa.String(100), nullable=False),
        sa.Column("event_bucket", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["pratikshya.users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["pratikshya.catalog_product.id"], ondelete="CASCADE", onupdate="CASCADE"),
        sa.CheckConstraint("event_type IN ('VIEW','CLICK','WISHLIST','UNWISHLIST','CART_ADD','CART_REMOVE','PURCHASE')", name="ck_interaction_event_type"),
        sa.UniqueConstraint("customer_id", "dedup_key", name="uq_interaction_retry"),
        sa.UniqueConstraint("customer_id", "product_id", "event_type", "event_bucket", name="uq_interaction_bucket"),
        schema="pratikshya",
    )
    for name, columns in (
        ("ix_user_product_interactions_id", ["id"]),
        ("ix_interaction_customer_recent", ["customer_id", "created_at"]),
        ("ix_interaction_product_recent", ["product_id", "created_at"]),
        ("ix_interaction_type_recent", ["event_type", "created_at"]),
        ("ix_interaction_retention", ["created_at"]),
    ):
        op.create_index(name, "user_product_interactions", columns, schema="pratikshya")


def downgrade():
    # Only behavioral history is removed. No business/catalogue/audit data touched.
    op.drop_table("user_product_interactions", schema="pratikshya")
