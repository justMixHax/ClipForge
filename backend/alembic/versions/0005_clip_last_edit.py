"""clip last prompt edit: what you asked for, what changed, and the clip before it (for undo)

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-23 16:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("clips") as batch:
        batch.add_column(sa.Column("last_edit", sa.JSON(), nullable=True))


def downgrade() -> None:
    # a rebuild must be told the table never reuses an id: SQLite does not report that rule
    with op.batch_alter_table("clips", table_kwargs={"sqlite_autoincrement": True}) as batch:
        batch.drop_column("last_edit")
