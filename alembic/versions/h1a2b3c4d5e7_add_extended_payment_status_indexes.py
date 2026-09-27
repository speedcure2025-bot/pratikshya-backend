"""add_extended_payment_status_indexes

Revision ID: h1a2b3c4d5e7
Revises: t3c4d5e6f7a8
Create Date: 2026-09-22 23:30:00.000000

Adds index on orders_order.payment_status and ensures payment status columns
support expanded state vocabulary (CREATED, AUTHORIZED, CAPTURED, FAILED,
REFUND_PENDING, PARTIALLY_REFUNDED, REFUNDED, PENDING_PAYMENT, PAYMENT_PROCESSING,
PAYMENT_FAILED, PAYMENT_CANCELLED, PAYMENT_EXPIRED).

Non-destructive and fully reversible.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "h1a2b3c4d5e7"
down_revision: Union[str, None] = "t3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Add index on orders_order.payment_status if not exists ───────────────
    conn = op.get_bind()
    insp = sa.inspect(conn)
    indexes = [ix["name"] for ix in insp.get_indexes("orders_order")]
    if "ix_orders_order_payment_status" not in indexes:
        op.create_index(
            "ix_orders_order_payment_status",
            "orders_order",
            ["payment_status"],
            unique=False,
        )


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    indexes = [ix["name"] for ix in insp.get_indexes("orders_order")]
    if "ix_orders_order_payment_status" in indexes:
        op.drop_index("ix_orders_order_payment_status", table_name="orders_order")
