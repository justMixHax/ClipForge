"""clip TikTok caption: the caption you wrote or saved for TikTok (empty: the automatic one)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-23 14:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("clips") as batch:
        batch.add_column(sa.Column("tiktok_caption", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("clips") as batch:
        batch.drop_column("tiktok_caption")
