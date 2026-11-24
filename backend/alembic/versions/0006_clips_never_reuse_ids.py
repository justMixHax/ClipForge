"""clips: put back the rule that an id is never used twice, where a rollback had dropped it

SQLite cannot drop a column in place, so rolling a change back rebuilds the table from what SQLite reports
about it, and that report does not include AUTOINCREMENT. A database that was once rolled back therefore gave a
new clip the id of the one deleted just before it, and an old link or notification opened the wrong clip.
Databases that never were rolled back already have the rule and are left alone.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-24 13:00:00
"""
from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

# Every later change to this table passes these, so a rebuild keeps the rule.
CLIPS = {"sqlite_autoincrement": True}


def upgrade() -> None:
    created = op.get_bind().exec_driver_sql(
        "select sql from sqlite_master where type = 'table' and name = 'clips'").scalar() or ""
    if "AUTOINCREMENT" in created.upper():
        return
    with op.batch_alter_table("clips", recreate="always", table_kwargs=CLIPS):
        pass


def downgrade() -> None:
    pass  # the rule does no harm to the earlier versions, and they asked for it too
