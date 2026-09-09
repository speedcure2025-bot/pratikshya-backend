"""add_orders_columns

Revision ID: e1f2a3b4c5d6
Revises: d1e2f3a4b5c6
Create Date: 2026-08-14 00:00:00.000000

Adds all business columns to the orders tables that were created bare
in the initial migration:
  - orders_order
  - orders_order_item
  - orders_order_status_history
  - orders_return_order
  - orders_return_item
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── orders_order ──────────────────────────────────────────────────────────
    op.add_column("orders_order", sa.Column("order_number", sa.String(50), nullable=True))
    op.add_column("orders_order", sa.Column("customer_id", sa.String(36), nullable=True))
    op.add_column("orders_order", sa.Column("guest_email", sa.String(255), nullable=True))
    op.add_column("orders_order", sa.Column("guest_phone", sa.String(20), nullable=True))
    op.add_column("orders_order", sa.Column("shipping_address", sa.JSON(), nullable=True))
    op.add_column("orders_order", sa.Column("delivery_method", sa.String(20), nullable=True, server_default="standard"))
    op.add_column("orders_order", sa.Column("payment_method", sa.String(30), nullable=True))
    op.add_column("orders_order", sa.Column("status", sa.String(50), nullable=True, server_default="ORDER_CONFIRMED"))
    op.add_column("orders_order", sa.Column("payment_status", sa.String(30), nullable=True, server_default="PENDING"))
    op.add_column("orders_order", sa.Column("subtotal", sa.Integer(), nullable=True, server_default="0"))
    op.add_column("orders_order", sa.Column("product_discount", sa.Integer(), nullable=True, server_default="0"))
    op.add_column("orders_order", sa.Column("coupon_discount", sa.Integer(), nullable=True, server_default="0"))
    op.add_column("orders_order", sa.Column("shipping_fee", sa.Integer(), nullable=True, server_default="0"))
    op.add_column("orders_order", sa.Column("cod_fee", sa.Integer(), nullable=True, server_default="0"))
    op.add_column("orders_order", sa.Column("total", sa.Integer(), nullable=True, server_default="0"))
    op.add_column("orders_order", sa.Column("coupon_code", sa.String(50), nullable=True))
    op.add_column("orders_order", sa.Column("coupon_id", sa.String(36), nullable=True))
    op.add_column("orders_order", sa.Column("customer_note", sa.Text(), nullable=True))
    op.add_column("orders_order", sa.Column("inventory_reservation_id", sa.String(36), nullable=True))
    op.add_column("orders_order", sa.Column("fulfillment_location_id", sa.String(36), nullable=True))
    op.add_column("orders_order", sa.Column("fulfillment_handler_id", sa.String(36), nullable=True))
    op.add_column("orders_order", sa.Column("tracking_number", sa.String(100), nullable=True))
    op.add_column("orders_order", sa.Column("carrier", sa.String(100), nullable=True))
    op.add_column("orders_order", sa.Column("estimated_delivery", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders_order", sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders_order", sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders_order", sa.Column("internal_notes", sa.JSON(), nullable=True))
    op.add_column("orders_order", sa.Column("timeline", sa.JSON(), nullable=True))
    op.add_column("orders_order", sa.Column("invoice_number", sa.String(100), nullable=True))
    op.add_column("orders_order", sa.Column("invoice_issued_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders_order", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders_order", sa.Column("cancellation_reason", sa.Text(), nullable=True))
    op.add_column("orders_order", sa.Column("cancelled_by", sa.String(36), nullable=True))

    # Back-fill nulls so NOT NULL can be enforced later if needed
    op.execute("UPDATE orders_order SET order_number = 'PF-ORD-LEGACY-' || id WHERE order_number IS NULL")
    op.execute("UPDATE orders_order SET payment_method = 'unknown' WHERE payment_method IS NULL")
    op.execute("UPDATE orders_order SET delivery_method = 'standard' WHERE delivery_method IS NULL")
    op.execute("UPDATE orders_order SET status = 'ORDER_CONFIRMED' WHERE status IS NULL")
    op.execute("UPDATE orders_order SET payment_status = 'PENDING' WHERE payment_status IS NULL")
    op.execute("UPDATE orders_order SET subtotal = 0 WHERE subtotal IS NULL")
    op.execute("UPDATE orders_order SET product_discount = 0 WHERE product_discount IS NULL")
    op.execute("UPDATE orders_order SET coupon_discount = 0 WHERE coupon_discount IS NULL")
    op.execute("UPDATE orders_order SET shipping_fee = 0 WHERE shipping_fee IS NULL")
    op.execute("UPDATE orders_order SET cod_fee = 0 WHERE cod_fee IS NULL")
    op.execute("UPDATE orders_order SET total = 0 WHERE total IS NULL")

    # Unique + index on order_number
    op.create_unique_constraint("uq_orders_order_number", "orders_order", ["order_number"])
    op.create_index("ix_orders_order_number", "orders_order", ["order_number"])
    op.create_index("ix_orders_order_customer_id", "orders_order", ["customer_id"])
    op.create_index("ix_orders_order_status", "orders_order", ["status"])

    # FK to users
    op.create_foreign_key(
        "fk_orders_order_customer_id",
        "orders_order", "users",
        ["customer_id"], ["id"],
        ondelete="SET NULL",
    )

    # ── orders_order_item ─────────────────────────────────────────────────────
    op.add_column("orders_order_item", sa.Column("order_id", sa.String(36), nullable=True))
    op.add_column("orders_order_item", sa.Column("product_id", sa.String(36), nullable=True))
    op.add_column("orders_order_item", sa.Column("product_name", sa.String(255), nullable=True, server_default=""))
    op.add_column("orders_order_item", sa.Column("product_image", sa.String(500), nullable=True))
    op.add_column("orders_order_item", sa.Column("sku", sa.String(100), nullable=True))
    op.add_column("orders_order_item", sa.Column("color", sa.String(100), nullable=True))
    op.add_column("orders_order_item", sa.Column("size", sa.String(50), nullable=True))
    op.add_column("orders_order_item", sa.Column("unit_price", sa.Integer(), nullable=True, server_default="0"))
    op.add_column("orders_order_item", sa.Column("original_price", sa.Integer(), nullable=True, server_default="0"))
    op.add_column("orders_order_item", sa.Column("quantity", sa.Integer(), nullable=True, server_default="1"))
    op.add_column("orders_order_item", sa.Column("line_total", sa.Integer(), nullable=True, server_default="0"))
    op.add_column("orders_order_item", sa.Column("returned_quantity", sa.Integer(), nullable=True, server_default="0"))

    op.execute("UPDATE orders_order_item SET unit_price = 0 WHERE unit_price IS NULL")
    op.execute("UPDATE orders_order_item SET original_price = 0 WHERE original_price IS NULL")
    op.execute("UPDATE orders_order_item SET quantity = 1 WHERE quantity IS NULL")
    op.execute("UPDATE orders_order_item SET line_total = 0 WHERE line_total IS NULL")
    op.execute("UPDATE orders_order_item SET returned_quantity = 0 WHERE returned_quantity IS NULL")
    op.execute("UPDATE orders_order_item SET product_name = '' WHERE product_name IS NULL")

    op.create_index("ix_orders_order_item_order_id", "orders_order_item", ["order_id"])
    op.create_index("ix_orders_order_item_product_id", "orders_order_item", ["product_id"])
    op.create_foreign_key(
        "fk_orders_order_item_order_id",
        "orders_order_item", "orders_order",
        ["order_id"], ["id"],
        ondelete="CASCADE",
    )

    # ── orders_order_status_history ───────────────────────────────────────────
    op.add_column("orders_order_status_history", sa.Column("order_id", sa.String(36), nullable=True))
    op.add_column("orders_order_status_history", sa.Column("from_status", sa.String(50), nullable=True))
    op.add_column("orders_order_status_history", sa.Column("to_status", sa.String(50), nullable=True, server_default=""))
    op.add_column("orders_order_status_history", sa.Column("actor_id", sa.String(36), nullable=True))
    op.add_column("orders_order_status_history", sa.Column("actor_name", sa.String(255), nullable=True))
    op.add_column("orders_order_status_history", sa.Column("note", sa.Text(), nullable=True))

    op.execute("UPDATE orders_order_status_history SET to_status = '' WHERE to_status IS NULL")

    op.create_index("ix_orders_order_status_history_order_id", "orders_order_status_history", ["order_id"])
    op.create_foreign_key(
        "fk_orders_order_status_history_order_id",
        "orders_order_status_history", "orders_order",
        ["order_id"], ["id"],
        ondelete="CASCADE",
    )

    # ── orders_return_order ───────────────────────────────────────────────────
    op.add_column("orders_return_order", sa.Column("order_id", sa.String(36), nullable=True))
    op.add_column("orders_return_order", sa.Column("return_number", sa.String(50), nullable=True))
    op.add_column("orders_return_order", sa.Column("customer_id", sa.String(36), nullable=True))
    op.add_column("orders_return_order", sa.Column("pickup_method", sa.String(30), nullable=True, server_default="SCHEDULED_PICKUP"))
    op.add_column("orders_return_order", sa.Column("status", sa.String(50), nullable=True, server_default="RETURN_REQUESTED"))
    op.add_column("orders_return_order", sa.Column("rejection_reason", sa.Text(), nullable=True))
    op.add_column("orders_return_order", sa.Column("rejection_reason_customer", sa.Text(), nullable=True))
    op.add_column("orders_return_order", sa.Column("package_condition", sa.String(50), nullable=True))
    op.add_column("orders_return_order", sa.Column("inspection_condition", sa.String(50), nullable=True))
    op.add_column("orders_return_order", sa.Column("inspection_notes", sa.Text(), nullable=True))
    op.add_column("orders_return_order", sa.Column("refund_amount", sa.Integer(), nullable=True, server_default="0"))
    op.add_column("orders_return_order", sa.Column("refund_status", sa.String(30), nullable=True, server_default="NOT_REQUESTED"))
    op.add_column("orders_return_order", sa.Column("refund_method", sa.String(50), nullable=True))
    op.add_column("orders_return_order", sa.Column("refund_initiated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders_return_order", sa.Column("refund_completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders_return_order", sa.Column("pickup_scheduled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders_return_order", sa.Column("pickup_address", sa.JSON(), nullable=True))
    op.add_column("orders_return_order", sa.Column("timeline", sa.JSON(), nullable=True))
    op.add_column("orders_return_order", sa.Column("reviewed_by", sa.String(36), nullable=True))
    op.add_column("orders_return_order", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))

    op.execute("UPDATE orders_return_order SET return_number = 'PF-RET-LEGACY-' || id WHERE return_number IS NULL")
    op.execute("UPDATE orders_return_order SET pickup_method = 'SCHEDULED_PICKUP' WHERE pickup_method IS NULL")
    op.execute("UPDATE orders_return_order SET status = 'RETURN_REQUESTED' WHERE status IS NULL")
    op.execute("UPDATE orders_return_order SET refund_amount = 0 WHERE refund_amount IS NULL")
    op.execute("UPDATE orders_return_order SET refund_status = 'NOT_REQUESTED' WHERE refund_status IS NULL")

    op.create_unique_constraint("uq_orders_return_number", "orders_return_order", ["return_number"])
    op.create_index("ix_orders_return_order_order_id", "orders_return_order", ["order_id"])
    op.create_index("ix_orders_return_order_customer_id", "orders_return_order", ["customer_id"])
    op.create_index("ix_orders_return_order_status", "orders_return_order", ["status"])
    op.create_foreign_key(
        "fk_orders_return_order_order_id",
        "orders_return_order", "orders_order",
        ["order_id"], ["id"],
        ondelete="CASCADE",
    )

    # ── orders_return_item ────────────────────────────────────────────────────
    op.add_column("orders_return_item", sa.Column("return_order_id", sa.String(36), nullable=True))
    op.add_column("orders_return_item", sa.Column("order_item_id", sa.String(36), nullable=True))
    op.add_column("orders_return_item", sa.Column("product_id", sa.String(36), nullable=True))
    op.add_column("orders_return_item", sa.Column("product_name", sa.String(255), nullable=True, server_default=""))
    op.add_column("orders_return_item", sa.Column("quantity", sa.Integer(), nullable=True, server_default="1"))
    op.add_column("orders_return_item", sa.Column("reason", sa.Text(), nullable=True))
    op.add_column("orders_return_item", sa.Column("refund_amount", sa.Integer(), nullable=True, server_default="0"))

    op.execute("UPDATE orders_return_item SET quantity = 1 WHERE quantity IS NULL")
    op.execute("UPDATE orders_return_item SET refund_amount = 0 WHERE refund_amount IS NULL")
    op.execute("UPDATE orders_return_item SET product_name = '' WHERE product_name IS NULL")

    op.create_index("ix_orders_return_item_return_order_id", "orders_return_item", ["return_order_id"])
    op.create_foreign_key(
        "fk_orders_return_item_return_order_id",
        "orders_return_item", "orders_return_order",
        ["return_order_id"], ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    # ── orders_return_item ────────────────────────────────────────────────────
    op.drop_constraint("fk_orders_return_item_return_order_id", "orders_return_item", type_="foreignkey")
    op.drop_index("ix_orders_return_item_return_order_id", "orders_return_item")
    for col in ["return_order_id", "order_item_id", "product_id", "product_name",
                "quantity", "reason", "refund_amount"]:
        op.drop_column("orders_return_item", col)

    # ── orders_return_order ───────────────────────────────────────────────────
    op.drop_constraint("fk_orders_return_order_order_id", "orders_return_order", type_="foreignkey")
    op.drop_index("ix_orders_return_order_status", "orders_return_order")
    op.drop_index("ix_orders_return_order_customer_id", "orders_return_order")
    op.drop_index("ix_orders_return_order_order_id", "orders_return_order")
    op.drop_constraint("uq_orders_return_number", "orders_return_order", type_="unique")
    for col in ["order_id", "return_number", "customer_id", "pickup_method", "status",
                "rejection_reason", "rejection_reason_customer", "package_condition",
                "inspection_condition", "inspection_notes", "refund_amount", "refund_status",
                "refund_method", "refund_initiated_at", "refund_completed_at",
                "pickup_scheduled_at", "pickup_address", "timeline", "reviewed_by", "reviewed_at"]:
        op.drop_column("orders_return_order", col)

    # ── orders_order_status_history ───────────────────────────────────────────
    op.drop_constraint("fk_orders_order_status_history_order_id", "orders_order_status_history", type_="foreignkey")
    op.drop_index("ix_orders_order_status_history_order_id", "orders_order_status_history")
    for col in ["order_id", "from_status", "to_status", "actor_id", "actor_name", "note"]:
        op.drop_column("orders_order_status_history", col)

    # ── orders_order_item ─────────────────────────────────────────────────────
    op.drop_constraint("fk_orders_order_item_order_id", "orders_order_item", type_="foreignkey")
    op.drop_index("ix_orders_order_item_product_id", "orders_order_item")
    op.drop_index("ix_orders_order_item_order_id", "orders_order_item")
    for col in ["order_id", "product_id", "product_name", "product_image", "sku",
                "color", "size", "unit_price", "original_price", "quantity",
                "line_total", "returned_quantity"]:
        op.drop_column("orders_order_item", col)

    # ── orders_order ──────────────────────────────────────────────────────────
    op.drop_constraint("fk_orders_order_customer_id", "orders_order", type_="foreignkey")
    op.drop_index("ix_orders_order_status", "orders_order")
    op.drop_index("ix_orders_order_customer_id", "orders_order")
    op.drop_index("ix_orders_order_number", "orders_order")
    op.drop_constraint("uq_orders_order_number", "orders_order", type_="unique")
    for col in ["order_number", "customer_id", "guest_email", "guest_phone",
                "shipping_address", "delivery_method", "payment_method", "status",
                "payment_status", "subtotal", "product_discount", "coupon_discount",
                "shipping_fee", "cod_fee", "total", "coupon_code", "coupon_id",
                "customer_note", "inventory_reservation_id", "fulfillment_location_id",
                "fulfillment_handler_id", "tracking_number", "carrier",
                "estimated_delivery", "delivered_at", "dispatched_at",
                "internal_notes", "timeline", "invoice_number", "invoice_issued_at",
                "cancelled_at", "cancellation_reason", "cancelled_by"]:
        op.drop_column("orders_order", col)
