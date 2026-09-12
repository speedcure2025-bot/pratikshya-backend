"""add_media_review_columns

Revision ID: e7d8e9f0a1b3
Revises: c7d8e9f0a1b2
Create Date: 2026-09-10

Adds business columns to pratikshya.media_media_review.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e7d8e9f0a1b3"
down_revision: Union[str, None] = "c7d8e9f0a1b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "pratikshya"
TABLE = "media_media_review"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("object_key", sa.String(length=512), nullable=True), schema=SCHEMA)
    op.add_column(TABLE, sa.Column("status", sa.String(length=20), server_default="PENDING", nullable=False), schema=SCHEMA)
    op.add_column(TABLE, sa.Column("submitted_by", sa.String(length=36), nullable=True), schema=SCHEMA)
    op.add_column(TABLE, sa.Column("reviewed_by", sa.String(length=36), nullable=True), schema=SCHEMA)
    op.add_column(TABLE, sa.Column("rejection_reason", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column(TABLE, sa.Column("comments", sa.Text(), nullable=True), schema=SCHEMA)

    op.create_index("ix_media_media_review_object_key", TABLE, ["object_key"], unique=False, schema=SCHEMA)
    op.create_index("ix_media_media_review_status", TABLE, ["status"], unique=False, schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_media_media_review_status", table_name=TABLE, schema=SCHEMA)
    op.drop_index("ix_media_media_review_object_key", table_name=TABLE, schema=SCHEMA)
    op.drop_column(TABLE, "comments", schema=SCHEMA)
    op.drop_column(TABLE, "rejection_reason", schema=SCHEMA)
    op.drop_column(TABLE, "reviewed_by", schema=SCHEMA)
    op.drop_column(TABLE, "submitted_by", schema=SCHEMA)
    op.drop_column(TABLE, "status", schema=SCHEMA)
    op.drop_column(TABLE, "object_key", schema=SCHEMA)
