"""add_employee_leave_table

Revision ID: f7d8e9f0a1b4
Revises: e7d8e9f0a1b3
Create Date: 2026-09-10

Creates pratikshya.employee_leave table for employee leave management.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f7d8e9f0a1b4"
down_revision: Union[str, None] = "e7d8e9f0a1b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "pratikshya"
TABLE = "employee_leave"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("employee_id", sa.String(length=36), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("leave_type", sa.String(length=50), server_default="CASUAL", nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="PENDING", nullable=False),
        sa.Column("reviewed_by", sa.String(length=36), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["employee_id"], [f"{SCHEMA}.employee_profiles.id"], ondelete="CASCADE"
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_employee_leave_id", TABLE, ["id"], unique=False, schema=SCHEMA)
    op.create_index("ix_employee_leave_employee_id", TABLE, ["employee_id"], unique=False, schema=SCHEMA)
    op.create_index("ix_employee_leave_status", TABLE, ["status"], unique=False, schema=SCHEMA)


def downgrade() -> None:
    op.drop_table(TABLE, schema=SCHEMA)
