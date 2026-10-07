"""demote_super_employee_to_employee

Revision ID: u1v2w3x4y5z6
Revises: t3c4d5e6f7a8
Create Date: 2026-09-21

Remove the SUPER_EMPLOYEE account level.  Any existing rows that carry
account_level = 'SUPER_EMPLOYEE' are reassigned to 'EMPLOYEE' so the
database stays consistent with the updated application code.  The SUPER_EMPLOYEE
role rows in the roles table and user_roles assignments are also cleaned up.
"""

from alembic import op
import sqlalchemy as sa

revision = "u1v2w3x4y5z6"
down_revision = "t3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Demote users whose account_level is SUPER_EMPLOYEE → EMPLOYEE
    conn.execute(
        sa.text(
            "UPDATE pratikshya.users "
            "SET account_level = 'EMPLOYEE' "
            "WHERE account_level = 'SUPER_EMPLOYEE'"
        )
    )

    # 2. Remove user_roles assignments for the SUPER_EMPLOYEE role
    conn.execute(
        sa.text(
            "DELETE FROM pratikshya.user_roles "
            "WHERE role_id IN ("
            "  SELECT id FROM pratikshya.roles WHERE name = 'SUPER_EMPLOYEE'"
            ")"
        )
    )

    # 3. Remove the SUPER_EMPLOYEE role row itself (if it exists)
    conn.execute(
        sa.text(
            "DELETE FROM pratikshya.roles WHERE name = 'SUPER_EMPLOYEE'"
        )
    )


def downgrade() -> None:
    # Re-create the SUPER_EMPLOYEE role row (without any user assignments —
    # those cannot be deterministically restored).
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "INSERT INTO pratikshya.roles (name, description, is_system) "
            "VALUES ('SUPER_EMPLOYEE', 'System role: Super Employee', TRUE) "
            "ON CONFLICT (name) DO NOTHING"
        )
    )
    # NOTE: account_level values are NOT rolled back — there is no safe way
    # to know which EMPLOYEE rows were previously SUPER_EMPLOYEE.
