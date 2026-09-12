"""ensure_pratikshya_users_account_level_columns

Revision ID: t3c4d5e6f7a8
Revises: s2a3b4c5d6e7
Create Date: 2026-09-11 16:00:00.000000

Idempotent safety net for the account-level schema drift.

``r1a2b3c4d5e6`` is the canonical revision that adds:

  • pratikshya.users.account_level
  • pratikshya.users.permission_mode
  • pratikshya.users.custom_permissions

A database can still lack those columns on ``pratikshya.users`` when:

  • r1 was never applied (app code moved ahead of the real DB), or
  • r1 ran without a schema qualifier and landed on ``public.users``, or
  • the version table was stamped past r1 without running its upgrade.

This revision re-invokes the same additive, non-destructive helper:
ADD COLUMN IF NOT EXISTS + backfill WHERE account_level IS NULL.
Existing user rows, passwords, sessions and role grants are preserved.
If r1 already applied correctly this upgrade is a no-op.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "t3c4d5e6f7a8"
down_revision: Union[str, None] = "s2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _r1_helper():
    path = Path(__file__).with_name("r1a2b3c4d5e6_add_account_level_columns.py")
    spec = importlib.util.spec_from_file_location("r1_account_level_columns", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.apply_account_level_columns


def upgrade() -> None:
    apply = _r1_helper()
    apply(op.get_bind())


def downgrade() -> None:
    # Non-destructive safety net: downgrade does not drop the columns.
    # Removing them would re-break staff login. Use r1's downgrade if a true
    # reverse of the original add is required.
    return
