"""add unique constraint on catalog_product.product_id display label

Revision ID: i1a2b3c4d5e8
Revises: h1a2b3c4d5e7
Create Date: 2026-10-06 00:00:00.000000

Adds a UNIQUE constraint on `pratikshya.catalog_product.product_id` (the
human-facing display label, e.g. PF-SAR-0001).

The service layer already enforces uniqueness with a SELECT-before-write 409,
but two concurrent change-id calls can race past it. This constraint closes
the race at the database level.

The de-duplication pre-check is a no-op on a clean database: the service has
always rejected duplicates so no two rows should share a display label. If
duplicates exist (manual inserts, data migration remnants) the upgrade will
raise an IntegrityError — resolve them first with:

    SELECT product_id, COUNT(*) FROM pratikshya.catalog_product
    GROUP BY product_id HAVING COUNT(*) > 1;
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "i1a2b3c4d5e8"
down_revision: Union[str, None] = "h1a2b3c4d5e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_catalog_product_product_id",
        "catalog_product",
        ["product_id"],
        unique=True,
        schema="pratikshya",
    )


def downgrade() -> None:
    op.drop_index(
        "uq_catalog_product_product_id",
        table_name="catalog_product",
        schema="pratikshya",
    )
