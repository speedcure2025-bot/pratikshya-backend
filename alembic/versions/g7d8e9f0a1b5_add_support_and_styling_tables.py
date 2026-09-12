"""add_support_and_styling_tables

Revision ID: g7d8e9f0a1b5
Revises: f7d8e9f0a1b4
Create Date: 2026-09-10

Creates pratikshya.employee_support_case and pratikshya.employee_styling_appointment tables.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "g7d8e9f0a1b5"
down_revision: Union[str, None] = "f7d8e9f0a1b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "pratikshya"
SUPPORT_TABLE = "employee_support_case"
STYLING_TABLE = "employee_styling_appointment"


def upgrade() -> None:
    # Support Cases Table
    op.create_table(
        SUPPORT_TABLE,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("customer_id", sa.String(length=36), nullable=True),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("priority", sa.String(length=20), server_default="MEDIUM", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="OPEN", nullable=False),
        sa.Column("assigned_employee_id", sa.String(length=36), nullable=True),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index("ix_employee_support_case_id", SUPPORT_TABLE, ["id"], unique=False, schema=SCHEMA)
    op.create_index("ix_employee_support_case_status", SUPPORT_TABLE, ["status"], unique=False, schema=SCHEMA)
    op.create_index("ix_employee_support_case_priority", SUPPORT_TABLE, ["priority"], unique=False, schema=SCHEMA)

    # Styling Appointments Table
    op.create_table(
        STYLING_TABLE,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("customer_id", sa.String(length=36), nullable=True),
        sa.Column("assigned_employee_id", sa.String(length=36), nullable=True),
        sa.Column("appointment_date", sa.Date(), nullable=False),
        sa.Column("appointment_time", sa.String(length=50), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="SCHEDULED", nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("preference_summary", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index("ix_employee_styling_appointment_id", STYLING_TABLE, ["id"], unique=False, schema=SCHEMA)
    op.create_index("ix_employee_styling_appointment_date", STYLING_TABLE, ["appointment_date"], unique=False, schema=SCHEMA)
    op.create_index("ix_employee_styling_appointment_status", STYLING_TABLE, ["status"], unique=False, schema=SCHEMA)


def downgrade() -> None:
    op.drop_table(STYLING_TABLE, schema=SCHEMA)
    op.drop_table(SUPPORT_TABLE, schema=SCHEMA)
