"""merge_attendance_and_employee_branches

Revision ID: 770a4ce325b5
Revises: ('u1v2w3x4y5z6', 'v2a3b4c5d6e7')
Create Date: 2026-10-07 21:56:17.122326

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '770a4ce325b5'
down_revision: Union[str, None] = ('u1v2w3x4y5z6', 'v2a3b4c5d6e7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
