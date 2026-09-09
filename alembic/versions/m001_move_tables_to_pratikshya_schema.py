"""move_tables_to_pratikshya_schema

Revision ID: m001_move_tables_to_pratikshya_schema
Revises: z1a2b3c4d5e6_add_wishlist_and_activity_columns
Create Date: 2026-08-24

This migration:
  1. Creates the 'pratikshya' PostgreSQL schema.
  2. Moves every application table from the default 'public' schema into 'pratikshya'.

Downgrade reverses this by moving tables back to 'public' and dropping the schema.
"""
from typing import Sequence, Union

from alembic import op

# ---------------------------------------------------------------------------
# Revision metadata
# ---------------------------------------------------------------------------
revision: str = "m001schema"
down_revision: Union[str, None] = "z1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ---------------------------------------------------------------------------
# All application tables (in safe migration order — parents before children)
# ---------------------------------------------------------------------------
ALL_TABLES: list[str] = [
    # standalone / root tables first
    "audit_activity_log",
    "auth_password_reset",
    "auth_verification_token",
    "permissions",
    "roles",
    "users",
    "catalog_category",
    "catalog_collection",
    "catalog_product",
    "catalog_product_tag",
    "catalog_tag",
    "chatbot_chat_retrieval",
    "chatbot_conversation",
    "chatbot_knowledge_chunk",
    "chatbot_knowledge_document",
    "chatbot_message",
    "checkout_checkout",
    "checkout_payment",
    "checkout_payment_transaction",
    "commerce_cart",
    "commerce_cart_item",
    "commerce_coupon",
    "commerce_coupon_redemption",
    "commerce_wishlist",
    "commerce_wishlist_item",
    "customer_address",
    "employee_department",
    "inventory_inventory_location",
    "inventory_inventory_movement",
    "inventory_inventory_stock",
    "inventory_stock_reservation",
    "inventory_stock_transfer",
    "inventory_warehouse",
    "media_marketing_media",
    "media_media_asset",
    "media_media_review",
    "media_product_media",
    "notification_notification",
    "orders_order",
    "orders_order_item",
    "orders_order_status_history",
    "orders_return_item",
    "orders_return_order",
    "pricing_price_history",
    "pricing_product_price",
    "pricing_tax_rate",
    "variants_attribute",
    "variants_attribute_value",
    "variants_product_attribute",
    "variants_product_variant",
    # tables with FK dependencies
    "customer_profiles",
    "employee_section",
    "oauth_accounts",
    "role_permissions",
    "user_roles",
    "user_sessions",
    "employee_profiles",
    "employee_attendance",
    "employee_performance",
    "employee_target",
    # alembic version table is managed by alembic itself — do NOT move it
]

SCHEMA_NAME = "pratikshya"


def upgrade() -> None:
    # 1. Create the new schema
    op.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA_NAME}"')

    # 2. Move every table from public → pratikshya safely
    for table in ALL_TABLES:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.tables 
                    WHERE table_schema = 'public' AND table_name = '{table}'
                ) AND NOT EXISTS (
                    SELECT 1 FROM information_schema.tables 
                    WHERE table_schema = '{SCHEMA_NAME}' AND table_name = '{table}'
                ) THEN
                    EXECUTE 'ALTER TABLE public."' || '{table}' || '" SET SCHEMA "{SCHEMA_NAME}"';
                END IF;
            END $$;
            """
        )

    # Update the search_path so sessions find tables without qualifying the schema name
    op.execute(f"SELECT set_config('search_path', '{SCHEMA_NAME},public', false)")
    bind = op.get_bind()
    db_name = bind.engine.url.database
    if db_name:
        op.execute(f'ALTER DATABASE "{db_name}" SET search_path TO "{SCHEMA_NAME}", public')


def downgrade() -> None:
    # Move every table back from pratikshya → public
    for table in reversed(ALL_TABLES):
        op.execute(
            f'ALTER TABLE IF EXISTS "{SCHEMA_NAME}"."{table}" SET SCHEMA public'
        )

    # Reset search_path to default public
    bind = op.get_bind()
    db_name = bind.engine.url.database
    if db_name:
        op.execute(f'ALTER DATABASE "{db_name}" SET search_path TO public')

    # Drop the schema (only if it is now empty)
    op.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA_NAME}"')
