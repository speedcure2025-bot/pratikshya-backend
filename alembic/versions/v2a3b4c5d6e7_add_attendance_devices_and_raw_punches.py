"""add_attendance_devices_and_raw_punches

Revision ID: v2a3b4c5d6e7
Revises: j1a2b3c4d5e9
Create Date: 2026-10-07 20:45:00.000000

Machine attendance foundation (phases 1-2). Additive only:

  • creates `attendance_devices` (registered punching machines)
  • creates `attendance_punches` (append-only raw punch log, de-duplicated on
    device + PIN + instant)
  • adds `employee_profiles.device_pin` (machine user ID, unique)
  • adds processed-result columns to `employee_attendance`
    (worked/late/early minutes, punch_count, exception, source). Existing rows
    keep NULLs and are marked source = 'WEB'.

Table names resolve through the `pratikshya` search_path set in env.py (same
convention as s2a3b4c5d6e7). Downgrade drops only what this revision added.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "v2a3b4c5d6e7"
down_revision: Union[str, None] = "j1a2b3c4d5e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── attendance_devices ───────────────────────────────────────────────────
    op.create_table(
        "attendance_devices",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("serial_number", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_attendance_devices_id", "attendance_devices", ["id"], unique=False)
    op.create_index(
        "ix_attendance_devices_serial_number", "attendance_devices", ["serial_number"], unique=True
    )

    # ── employee_profiles.device_pin ─────────────────────────────────────────
    op.add_column("employee_profiles", sa.Column("device_pin", sa.String(length=32), nullable=True))
    op.create_index(
        "ix_employee_profiles_device_pin", "employee_profiles", ["device_pin"], unique=True
    )

    # ── attendance_punches ───────────────────────────────────────────────────
    op.create_table(
        "attendance_punches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("device_id", sa.String(length=36), nullable=True),
        sa.Column("device_pin", sa.String(length=32), nullable=False),
        sa.Column("employee_id", sa.String(length=36), nullable=True),
        sa.Column("punched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False, server_default="UNKNOWN"),
        sa.Column("verify_type", sa.String(length=20), nullable=True),
        sa.Column("source", sa.String(length=10), nullable=False, server_default="DEVICE"),
        sa.Column("raw_line", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["device_id"], ["attendance_devices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["employee_id"], ["employee_profiles.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "device_id", "device_pin", "punched_at", name="uq_attendance_punch_device_pin_time"
        ),
    )
    op.create_index("ix_attendance_punches_id", "attendance_punches", ["id"], unique=False)
    op.create_index("ix_attendance_punches_device_id", "attendance_punches", ["device_id"], unique=False)
    op.create_index("ix_attendance_punches_device_pin", "attendance_punches", ["device_pin"], unique=False)
    op.create_index("ix_attendance_punches_employee_id", "attendance_punches", ["employee_id"], unique=False)
    op.create_index("ix_attendance_punches_punched_at", "attendance_punches", ["punched_at"], unique=False)
    op.create_index(
        "ix_attendance_punches_employee_punched_at",
        "attendance_punches",
        ["employee_id", "punched_at"],
        unique=False,
    )

    # ── employee_attendance processed-result columns ─────────────────────────
    op.add_column("employee_attendance", sa.Column("worked_minutes", sa.Integer(), nullable=True))
    op.add_column("employee_attendance", sa.Column("late_minutes", sa.Integer(), nullable=True))
    op.add_column("employee_attendance", sa.Column("early_leave_minutes", sa.Integer(), nullable=True))
    op.add_column("employee_attendance", sa.Column("punch_count", sa.Integer(), nullable=True))
    op.add_column("employee_attendance", sa.Column("exception", sa.String(length=30), nullable=True))
    op.add_column(
        "employee_attendance",
        sa.Column("source", sa.String(length=10), nullable=False, server_default="WEB"),
    )


def downgrade() -> None:
    op.drop_column("employee_attendance", "source")
    op.drop_column("employee_attendance", "exception")
    op.drop_column("employee_attendance", "punch_count")
    op.drop_column("employee_attendance", "early_leave_minutes")
    op.drop_column("employee_attendance", "late_minutes")
    op.drop_column("employee_attendance", "worked_minutes")

    op.drop_index("ix_attendance_punches_employee_punched_at", table_name="attendance_punches")
    op.drop_index("ix_attendance_punches_punched_at", table_name="attendance_punches")
    op.drop_index("ix_attendance_punches_employee_id", table_name="attendance_punches")
    op.drop_index("ix_attendance_punches_device_pin", table_name="attendance_punches")
    op.drop_index("ix_attendance_punches_device_id", table_name="attendance_punches")
    op.drop_index("ix_attendance_punches_id", table_name="attendance_punches")
    op.drop_table("attendance_punches")

    op.drop_index("ix_employee_profiles_device_pin", table_name="employee_profiles")
    op.drop_column("employee_profiles", "device_pin")

    op.drop_index("ix_attendance_devices_serial_number", table_name="attendance_devices")
    op.drop_index("ix_attendance_devices_id", table_name="attendance_devices")
    op.drop_table("attendance_devices")
