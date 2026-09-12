"""add_account_level_and_custom_permissions

Revision ID: r1a2b3c4d5e6
Revises: b6b5dcfb675b, d8e9f0a1b2c3
Create Date: 2026-09-11 00:00:00.000000

Unified authentication + 4-level RBAC consolidation (additive, non-destructive).

THIS is the canonical revision that brings ``pratikshya.users`` to the
columns the User ORM maps:

  • pratikshya.users.account_level      SUPER_ADMIN | ADMIN | SUPER_EMPLOYEE | EMPLOYEE
                                        (NULL for customers)
  • pratikshya.users.permission_mode    'role' | 'custom'
  • pratikshya.users.custom_permissions explicit grant list for permission_mode='custom'

Columns are added with fully-qualified ``pratikshya.users`` identifiers
(``ADD COLUMN IF NOT EXISTS``) so the revision cannot land on ``public.users``
when ``search_path`` is unset, and is safe to re-apply if a previous run
missed the real schema.

Deterministic backfill (existing passwords, sessions and role rows are
untouched; no rows are deleted):

  1. admin + SUPER_ADMIN role     → SUPER_ADMIN
  2. remaining admin rows         → ADMIN
  3. employee + SUPER_EMPLOYEE    → SUPER_EMPLOYEE
  4. remaining employee rows      → EMPLOYEE
  5. customers stay NULL

``permission_mode`` defaults to ``'role'`` for any staff row that is still
NULL; ``custom_permissions`` stays NULL (legacy role grants keep working).
Downgrade drops only these three columns.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "r1a2b3c4d5e6"
down_revision: Union[str, None] = ("b6b5dcfb675b", "d8e9f0a1b2c3")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "pratikshya"
USERS = f"{SCHEMA}.users"
ROLES = f"{SCHEMA}.roles"
USER_ROLES = f"{SCHEMA}.user_roles"

ACCOUNT_LEVEL_COLUMNS = ("account_level", "permission_mode", "custom_permissions")


def _existing_user_columns(connection) -> set:
    inspector = sa.inspect(connection)
    for schema in (SCHEMA, None):
        try:
            cols = inspector.get_columns("users", schema=schema)
        except Exception:
            continue
        if cols:
            return {c["name"] for c in cols}
    return set()


def _ident(connection, table: str) -> str:
    """Qualify application tables for PostgreSQL; keep SQLite attach-friendly."""
    if connection.dialect.name == "postgresql":
        return f"{SCHEMA}.{table}"
    existing = _existing_user_columns(connection)
    # Prefer the attached pratikshya schema when the test fixture created it.
    try:
        inspector = sa.inspect(connection)
        inspector.get_columns("users", schema=SCHEMA)
        return f"{SCHEMA}.{table}"
    except Exception:
        if existing:
            return table
        return f"{SCHEMA}.{table}"


def apply_account_level_columns(connection) -> None:
    """
    Idempotent, non-destructive alignment of ``users`` with the User ORM.

    Safe for a database that already contains seeded admins: only ADD COLUMN
    and UPDATE ... WHERE account_level IS NULL. No DELETE, no password reset.
    """
    dialect = connection.dialect.name
    users = _ident(connection, "users")
    roles = _ident(connection, "roles")
    user_roles = _ident(connection, "user_roles")
    existing = _existing_user_columns(connection)

    if dialect == "postgresql":
        connection.execute(
            sa.text(
                f"""
                ALTER TABLE {USERS}
                    ADD COLUMN IF NOT EXISTS account_level VARCHAR(20),
                    ADD COLUMN IF NOT EXISTS permission_mode VARCHAR(10) DEFAULT 'role',
                    ADD COLUMN IF NOT EXISTS custom_permissions JSONB
                """
            )
        )
        connection.execute(
            sa.text(
                f"COMMENT ON COLUMN {USERS}.account_level IS "
                "'Staff hierarchy level: SUPER_ADMIN | ADMIN | SUPER_EMPLOYEE | EMPLOYEE; NULL for customers'"
            )
        )
        connection.execute(
            sa.text(
                f"COMMENT ON COLUMN {USERS}.permission_mode IS "
                "'role = capability set comes from assigned roles; custom = custom_permissions override'"
            )
        )
        connection.execute(
            sa.text(
                f"COMMENT ON COLUMN {USERS}.custom_permissions IS "
                "'Explicit grant list (canonical capability or legacy granular codes) when permission_mode=custom'"
            )
        )
    else:
        # SQLite (unit tests): ADD COLUMN one at a time; IF NOT EXISTS from 3.35+.
        type_sql = {
            "account_level": "VARCHAR(20)",
            "permission_mode": "VARCHAR(10) DEFAULT 'role'",
            "custom_permissions": "TEXT",
        }
        for name in ACCOUNT_LEVEL_COLUMNS:
            if name in existing:
                continue
            connection.execute(sa.text(f"ALTER TABLE {users} ADD COLUMN {name} {type_sql[name]}"))

    # ── Deterministic backfill ─────────────────────────────────────────────
    # 1) Admins holding the SUPER_ADMIN role become SUPER_ADMIN.
    connection.execute(
        sa.text(
            f"""
            UPDATE {users}
               SET account_level = 'SUPER_ADMIN'
             WHERE user_type = 'admin'
               AND account_level IS NULL
               AND id IN (
                    SELECT ur.user_id
                      FROM {user_roles} ur
                      JOIN {roles} r ON r.id = ur.role_id
                     WHERE r.name = 'SUPER_ADMIN'
               )
            """
        )
    )
    # 2) Every remaining admin becomes ADMIN (never above SUPER_ADMIN).
    connection.execute(
        sa.text(
            f"""
            UPDATE {users}
               SET account_level = 'ADMIN'
             WHERE user_type = 'admin'
               AND account_level IS NULL
            """
        )
    )
    # 3) Employees holding the SUPER_EMPLOYEE marker/role keep that level.
    #    Must run BEFORE the generic EMPLOYEE fill: resolve_account_level
    #    prefers the stored column, so writing EMPLOYEE here would demote them.
    connection.execute(
        sa.text(
            f"""
            UPDATE {users}
               SET account_level = 'SUPER_EMPLOYEE'
             WHERE user_type = 'employee'
               AND account_level IS NULL
               AND id IN (
                    SELECT ur.user_id
                      FROM {user_roles} ur
                      JOIN {roles} r ON r.id = ur.role_id
                     WHERE r.name = 'SUPER_EMPLOYEE'
               )
            """
        )
    )
    # 4) Remaining employees become EMPLOYEE (the pre-existing single level).
    connection.execute(
        sa.text(
            f"""
            UPDATE {users}
               SET account_level = 'EMPLOYEE'
             WHERE user_type = 'employee'
               AND account_level IS NULL
            """
        )
    )
    # permission_mode: staff rows with no explicit mode keep the role-derived set.
    connection.execute(
        sa.text(
            f"""
            UPDATE {users}
               SET permission_mode = 'role'
             WHERE permission_mode IS NULL
               AND user_type IN ('admin', 'employee')
            """
        )
    )


def upgrade() -> None:
    apply_account_level_columns(op.get_bind())


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.execute(
            sa.text(
                f"""
                ALTER TABLE {USERS}
                    DROP COLUMN IF EXISTS custom_permissions,
                    DROP COLUMN IF EXISTS permission_mode,
                    DROP COLUMN IF EXISTS account_level
                """
            )
        )
        return
    users = _ident(bind, "users")
    existing = _existing_user_columns(bind)
    for column in reversed(ACCOUNT_LEVEL_COLUMNS):
        if column in existing:
            op.execute(sa.text(f"ALTER TABLE {users} DROP COLUMN {column}"))
