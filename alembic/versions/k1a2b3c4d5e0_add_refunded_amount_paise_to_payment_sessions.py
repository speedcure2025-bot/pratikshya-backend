"""add_refunded_amount_paise_to_payment_sessions

Revision ID: k1a2b3c4d5e0
Revises: 770a4ce325b5
Create Date: 2026-10-08 12:00:00.000000

The PaymentSessionModel declares `refunded_amount_paise` but the original
`f1a2b3c4d5e6_add_payment_sessions_table` migration never included it.

Any code path that reads or writes this column (refund_payment,
_on_refund_processed, reconcile_batch, admin_list_sessions) will raise a
PostgreSQL "column does not exist" error on databases built from migrations.

This migration adds the missing column with a DEFAULT 0 so that existing rows
are back-filled immediately and no application-level NOT NULL constraint is
violated.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic
revision = "k1a2b3c4d5e0"
down_revision = "770a4ce325b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payment_sessions",
        sa.Column(
            "refunded_amount_paise",
            sa.Integer(),
            nullable=True,
            server_default="0",
            comment="Cumulative refunded amount in paise",
        ),
    )


def downgrade() -> None:
    op.drop_column("payment_sessions", "refunded_amount_paise")
