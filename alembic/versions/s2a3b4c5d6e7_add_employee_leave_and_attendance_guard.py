"""add_employee_leave_and_attendance_punch_guard

Revision ID: s2a3b4c5d6e7
Revises: r1a2b3c4d5e6
Create Date: 2026-09-11 12:00:00.000000

Workforce hardening pass (Phases 4–6). Additive except for ONE proven-safe
dedupe:

  • creates `employee_leave` (leave requests; no balance ledger exists in
    this product, so none is added)
  • enforces one attendance row per (employee, date) — the punch-duplicate
    guarantee in the mandate. Because the old admin attendance endpoint could
    insert a second row for a day that already had one, the migration first
    collapses duplicates DETERMINISTICALLY, keeping the most recently updated
    row per (employee_id, attendance_date); every other row is untouched.
    Downgrade drops the index and the new table only.

Table names resolve through the `pratikshya` search_path set in env.py (same
convention as r1a2b3c4d5e6 and z1a2b3c4d5e6).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "s2a3b4c5d6e7"
down_revision: Union[str, None] = "r1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── employee_leave ───────────────────────────────────────────────────────
    op.create_table(
        "employee_leave",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("employee_id", sa.String(length=36), nullable=False),
        sa.Column("leave_type", sa.String(length=20), nullable=False, server_default="OTHER"),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.String(length=36), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["employee_id"], ["employee_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_employee_leave_id", "employee_leave", ["id"], unique=False)
    op.create_index("ix_employee_leave_employee_id", "employee_leave", ["employee_id"], unique=False)
    op.create_index("ix_employee_leave_start_date", "employee_leave", ["start_date"], unique=False)
    op.create_index("ix_employee_leave_status", "employee_leave", ["status"], unique=False)

    # ── attendance duplicate-day collapse + guard index ──────────────────────
    op.execute(
        """
        DELETE FROM employee_attendance a
        USING employee_attendance b
        WHERE a.employee_id = b.employee_id
          AND a.attendance_date = b.attendance_date
          AND (a.updated_at < b.updated_at
               OR (a.updated_at = b.updated_at AND a.id < b.id))
        """
    )
    op.create_index(
        "uq_employee_attendance_employee_date",
        "employee_attendance",
        ["employee_id", "attendance_date"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_employee_attendance_employee_date", table_name="employee_attendance")
    op.drop_index("ix_employee_leave_status", table_name="employee_leave")
    op.drop_index("ix_employee_leave_start_date", table_name="employee_leave")
    op.drop_index("ix_employee_leave_employee_id", table_name="employee_leave")
    op.drop_index("ix_employee_leave_id", table_name="employee_leave")
    op.drop_table("employee_leave")
