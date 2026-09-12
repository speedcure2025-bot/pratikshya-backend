"""
add_inventory_business_columns

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-09-09

Adds business columns to all inventory tables in pratikshya schema:
- inventory_warehouse
- inventory_inventory_stock
- inventory_inventory_movement
- inventory_stock_reservation
- inventory_stock_transfer
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "d8e9f0a1b2c3"
down_revision: Union[str, None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "pratikshya"


def upgrade() -> None:
    # 1. inventory_warehouse
    op.add_column("inventory_warehouse", sa.Column("name", sa.String(255), nullable=False, server_default=""), schema=SCHEMA)
    op.add_column("inventory_warehouse", sa.Column("code", sa.String(100), nullable=False, server_default=""), schema=SCHEMA)
    op.add_column("inventory_warehouse", sa.Column("address", sa.String(500), nullable=True), schema=SCHEMA)
    op.add_column("inventory_warehouse", sa.Column("type", sa.String(50), nullable=False, server_default="WAREHOUSE"), schema=SCHEMA)
    op.add_column("inventory_warehouse", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")), schema=SCHEMA)

    # 2. inventory_inventory_stock
    op.add_column("inventory_inventory_stock", sa.Column("product_id", sa.String(64), nullable=True), schema=SCHEMA)
    op.add_column("inventory_inventory_stock", sa.Column("variant_id", sa.String(100), nullable=True), schema=SCHEMA)
    op.add_column("inventory_inventory_stock", sa.Column("sku", sa.String(100), nullable=False, server_default=""), schema=SCHEMA)
    op.add_column("inventory_inventory_stock", sa.Column("warehouse_id", sa.String(64), nullable=True), schema=SCHEMA)
    op.add_column("inventory_inventory_stock", sa.Column("on_hand", sa.Integer(), nullable=False, server_default="0"), schema=SCHEMA)
    op.add_column("inventory_inventory_stock", sa.Column("reserved", sa.Integer(), nullable=False, server_default="0"), schema=SCHEMA)
    op.add_column("inventory_inventory_stock", sa.Column("available", sa.Integer(), nullable=False, server_default="0"), schema=SCHEMA)
    op.add_column("inventory_inventory_stock", sa.Column("low_threshold", sa.Integer(), nullable=False, server_default="5"), schema=SCHEMA)

    # 3. inventory_inventory_movement
    op.add_column("inventory_inventory_movement", sa.Column("stock_id", sa.String(64), nullable=True), schema=SCHEMA)
    op.add_column("inventory_inventory_movement", sa.Column("delta", sa.Integer(), nullable=False, server_default="0"), schema=SCHEMA)
    op.add_column("inventory_inventory_movement", sa.Column("type", sa.String(50), nullable=False, server_default="ADJUST"), schema=SCHEMA)
    op.add_column("inventory_inventory_movement", sa.Column("reason", sa.String(255), nullable=True), schema=SCHEMA)
    op.add_column("inventory_inventory_movement", sa.Column("actor_id", sa.String(64), nullable=True), schema=SCHEMA)
    op.add_column("inventory_inventory_movement", sa.Column("actor_name", sa.String(255), nullable=True), schema=SCHEMA)

    # 4. inventory_stock_reservation
    op.add_column("inventory_stock_reservation", sa.Column("stock_id", sa.String(64), nullable=True), schema=SCHEMA)
    op.add_column("inventory_stock_reservation", sa.Column("order_id", sa.String(64), nullable=True), schema=SCHEMA)
    op.add_column("inventory_stock_reservation", sa.Column("cart_id", sa.String(64), nullable=True), schema=SCHEMA)
    op.add_column("inventory_stock_reservation", sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"), schema=SCHEMA)
    op.add_column("inventory_stock_reservation", sa.Column("status", sa.String(50), nullable=False, server_default="ACTIVE"), schema=SCHEMA)
    op.add_column("inventory_stock_reservation", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True), schema=SCHEMA)

    # 5. inventory_stock_transfer
    op.add_column("inventory_stock_transfer", sa.Column("from_warehouse_id", sa.String(64), nullable=True), schema=SCHEMA)
    op.add_column("inventory_stock_transfer", sa.Column("to_warehouse_id", sa.String(64), nullable=True), schema=SCHEMA)
    op.add_column("inventory_stock_transfer", sa.Column("status", sa.String(50), nullable=False, server_default="DRAFT"), schema=SCHEMA)
    op.add_column("inventory_stock_transfer", sa.Column("notes", sa.String(500), nullable=True), schema=SCHEMA)
    op.add_column("inventory_stock_transfer", sa.Column("created_by", sa.String(64), nullable=True), schema=SCHEMA)
    op.add_column("inventory_stock_transfer", sa.Column("lines", sa.JSON(), nullable=True), schema=SCHEMA)


def downgrade() -> None:
    pass
