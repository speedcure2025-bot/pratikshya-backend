"""backfill_inventory_stock_for_existing_products

Revision ID: l1a2b3c4d5e1
Revises: k1a2b3c4d5e0
Create Date: 2026-10-12 20:00:00.000000

Products added before the inventory auto-init fix (create_product /
create_draft now call InventoryService.create_stock_record automatically)
have no row in inventory_inventory_stock. This migration:

  1. Ensures the business columns on inventory_inventory_stock exist
     (idempotent ADD COLUMN IF NOT EXISTS) — guards against databases that
     were seeded via SQLAlchemy create_all rather than through the full
     migration chain, where d8e9f0a1b2c3 may have been stamped but not
     actually executed against the live schema.

  2. Inserts a zero-quantity stock record for every existing product that
     does not already have one.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic
revision = "l1a2b3c4d5e1"
down_revision = "k1a2b3c4d5e0"
branch_labels = None
depends_on = None

SCHEMA = "pratikshya"


def upgrade() -> None:
    # ── Step 1: ensure business columns exist (idempotent) ──────────────────
    # These match d8e9f0a1b2c3 exactly. ADD COLUMN IF NOT EXISTS is a no-op
    # when the column is already present, so running twice is safe.
    op.execute(sa.text("""
        ALTER TABLE pratikshya.inventory_inventory_stock
            ADD COLUMN IF NOT EXISTS product_id   VARCHAR(64)  DEFAULT NULL,
            ADD COLUMN IF NOT EXISTS variant_id   VARCHAR(100) DEFAULT NULL,
            ADD COLUMN IF NOT EXISTS sku          VARCHAR(100) NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS warehouse_id VARCHAR(64)  DEFAULT NULL,
            ADD COLUMN IF NOT EXISTS on_hand      INTEGER      NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS reserved     INTEGER      NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS available    INTEGER      NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS low_threshold INTEGER     NOT NULL DEFAULT 5
    """))

    # Also ensure inventory_inventory_movement columns exist
    op.execute(sa.text("""
        ALTER TABLE pratikshya.inventory_inventory_movement
            ADD COLUMN IF NOT EXISTS stock_id   VARCHAR(64)  DEFAULT NULL,
            ADD COLUMN IF NOT EXISTS delta      INTEGER      NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS type       VARCHAR(50)  NOT NULL DEFAULT 'ADJUST',
            ADD COLUMN IF NOT EXISTS reason     VARCHAR(255) DEFAULT NULL,
            ADD COLUMN IF NOT EXISTS actor_id   VARCHAR(64)  DEFAULT NULL,
            ADD COLUMN IF NOT EXISTS actor_name VARCHAR(255) DEFAULT NULL
    """))

    # ── Step 2: backfill missing stock rows for existing products ────────────
    op.execute(sa.text("""
        INSERT INTO pratikshya.inventory_inventory_stock
            (id, created_at, updated_at, product_id, variant_id, sku,
             warehouse_id, on_hand, reserved, available, low_threshold)
        SELECT
            gen_random_uuid()::text,
            now(),
            now(),
            p.id,
            NULL,
            COALESCE(p.sku, ''),
            NULL,
            COALESCE(p.stock, 0),
            0,
            COALESCE(p.stock, 0),
            COALESCE(p.low_stock_threshold, 5)
        FROM pratikshya.catalog_product p
        WHERE NOT EXISTS (
            SELECT 1
            FROM pratikshya.inventory_inventory_stock i
            WHERE i.product_id = p.id
              AND i.variant_id IS NULL
              AND i.warehouse_id IS NULL
        )
    """))


def downgrade() -> None:
    # Remove only the backfilled zero-stock rows (Step 2). The column
    # additions (Step 1) are left in place — removing them is destructive
    # and unnecessary for a downgrade of just the backfill.
    op.execute(sa.text("""
        DELETE FROM pratikshya.inventory_inventory_stock
        WHERE on_hand = 0
          AND reserved = 0
          AND warehouse_id IS NULL
          AND variant_id IS NULL
    """))
