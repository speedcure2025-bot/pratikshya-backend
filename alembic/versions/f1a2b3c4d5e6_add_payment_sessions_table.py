"""add_payment_sessions_table

Revision ID: f1a2b3c4d5e6
Revises: e1f2a3b4c5d6
Create Date: 2026-08-22 12:00:00.000000

Adds the `payment_sessions` table for the Razorpay payment gateway integration.

Each row tracks one payment attempt:
  - order_id          → FK to orders_order.id
  - razorpay_order_id → returned by Razorpay Create Order API
  - razorpay_payment_id / razorpay_signature → set after successful payment
  - amount_paise      → amount in paise (₹1 = 100 paise)
  - status            → CREATED | PENDING | PAID | FAILED | CANCELLED | EXPIRED
  - idempotency_key   → prevents duplicate session creation
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic
revision = "f1a2b3c4d5e6"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payment_sessions",

        # ── Primary key (UUID string) ──────────────────────────────────────────
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),

        # ── FK to orders_order ─────────────────────────────────────────────────
        sa.Column(
            "order_id",
            sa.String(36),
            sa.ForeignKey("orders_order.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),

        # ── Razorpay identifiers ───────────────────────────────────────────────
        sa.Column("razorpay_order_id",    sa.String(100),  nullable=True, unique=True),
        sa.Column("razorpay_payment_id",  sa.String(100),  nullable=True),
        sa.Column("razorpay_signature",   sa.String(255),  nullable=True),

        # ── Amount & currency ──────────────────────────────────────────────────
        sa.Column("amount_paise",  sa.Integer(),   nullable=False),
        sa.Column("currency",      sa.String(3),   nullable=False, server_default="INR"),

        # ── Payment method ─────────────────────────────────────────────────────
        sa.Column("payment_method", sa.String(30), nullable=False),

        # ── Session status ─────────────────────────────────────────────────────
        sa.Column("status", sa.String(30), nullable=False, server_default="CREATED"),

        # ── Timestamps ────────────────────────────────────────────────────────
        sa.Column("paid_at",       sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at",  sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at",    sa.DateTime(timezone=True), nullable=True),

        # ── Failure info ───────────────────────────────────────────────────────
        sa.Column("failure_reason",      sa.Text(),       nullable=True),
        sa.Column("failure_code",        sa.String(100),  nullable=True),

        # ── Webhook / misc ─────────────────────────────────────────────────────
        sa.Column("last_webhook_event",  sa.String(100),  nullable=True),
        sa.Column("idempotency_key",     sa.String(100),  nullable=True, unique=True),
        sa.Column("razorpay_receipt",    sa.String(100),  nullable=True),
        sa.Column("razorpay_notes",      sa.Text(),       nullable=True),

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
    )

    # Note: ix_payment_sessions_id and ix_payment_sessions_order_id are created
    # automatically by index=True on the column definitions above.
    op.create_index(
        "ix_payment_sessions_razorpay_order_id",
        "payment_sessions",
        ["razorpay_order_id"],
        unique=True,
    )
    op.create_index(
        "ix_payment_sessions_razorpay_payment_id",
        "payment_sessions",
        ["razorpay_payment_id"],
        unique=False,
    )
    op.create_index(
        "ix_payment_sessions_status",
        "payment_sessions",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_payment_sessions_idempotency_key",
        "payment_sessions",
        ["idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_payment_sessions_idempotency_key", table_name="payment_sessions")
    op.drop_index("ix_payment_sessions_status",          table_name="payment_sessions")
    op.drop_index("ix_payment_sessions_razorpay_payment_id", table_name="payment_sessions")
    op.drop_index("ix_payment_sessions_razorpay_order_id",   table_name="payment_sessions")
    op.drop_table("payment_sessions")
