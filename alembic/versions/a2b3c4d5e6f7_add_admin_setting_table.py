"""add_admin_setting_table

Revision ID: a2b3c4d5e6f7
Revises: m001schema
Create Date: 2026-08-14 00:00:00.000000

Creates the `admin_setting` table used to persist per-section JSON configuration.
One row per section key (e.g. "notifications", "shipping", "payments").
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, None] = "m001schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_setting",
        sa.Column(
            "id",
            sa.String(length=64),
            nullable=False,
            comment="Setting section key, e.g. 'notifications', 'shipping'.",
        ),
        # JSONB on Postgres; TEXT on SQLite (test / local dev without PG)
        sa.Column(
            "value",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.Text(), "sqlite"),
            nullable=False,
            server_default="{}",
            comment="Section configuration stored as JSONB.",
        ),
        sa.Column(
            "updated_by",
            sa.String(length=36),
            nullable=True,
            comment="User id of the last editor.",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Base columns expected by SQLAlchemy ORM for any model inheriting Base
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_admin_setting_id"), "admin_setting", ["id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_admin_setting_id"), table_name="admin_setting")
    op.drop_table("admin_setting")
