"""clip thumbnails: the words on each clip's thumbnail, the word in colour, its moment and its file

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-23 12:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("clips") as batch:
        batch.add_column(sa.Column("cover_text", sa.String(length=120), nullable=False, server_default=""))
        batch.add_column(sa.Column("cover_word", sa.String(length=60), nullable=False, server_default=""))
        batch.add_column(sa.Column("cover_time", sa.Float(), nullable=True))
        batch.add_column(sa.Column("cover_path", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    # a rebuild must be told the table never reuses an id: SQLite does not report that rule
    with op.batch_alter_table("clips", table_kwargs={"sqlite_autoincrement": True}) as batch:
        batch.drop_column("cover_path")
        batch.drop_column("cover_time")
        batch.drop_column("cover_word")
        batch.drop_column("cover_text")
