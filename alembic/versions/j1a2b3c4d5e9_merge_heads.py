"""merge_heads

Revision ID: j1a2b3c4d5e9
Revises: u1v2w3x4y5z6, i1a2b3c4d5e8
Create Date: 2026-10-07 00:00:00.000000

Merge revision that joins the two independent heads:
  - u1v2w3x4y5z6 (demote_super_employee_to_employee)
  - i1a2b3c4d5e8 (add_unique_product_display_id)

Both branches descend from t3c4d5e6f7a8 and are fully independent
(no overlapping table/column changes), so merging is safe.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "j1a2b3c4d5e9"
down_revision: Union[str, tuple] = ("u1v2w3x4y5z6", "i1a2b3c4d5e8")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
