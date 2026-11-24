"""creator discovery: the channels you follow and the uploads found on them

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-24 14:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "creators",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("external_id", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("handle", sa.String(length=200), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("thumb_url", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("subscribers", sa.Integer(), nullable=True),
        sa.Column("video_count", sa.Integer(), nullable=True),
        sa.Column("uploads_playlist", sa.String(length=100), nullable=False),
        sa.Column("permission_status", sa.String(length=16), nullable=False),
        sa.Column("permission_note", sa.Text(), nullable=False),
        sa.Column("check_error", sa.Text(), nullable=False),
        sa.Column("followed_at", sa.DateTime(), nullable=False),
        sa.Column("checked_at", sa.DateTime(), nullable=True),
        sa.Column("tried_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_creators_platform_external", "creators", ["platform", "external_id"], unique=True)
    op.create_table(
        "creator_videos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("creator_id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("duration", sa.Float(), nullable=False),
        sa.Column("live", sa.String(length=16), nullable=False),
        sa.Column("was_live", sa.Boolean(), nullable=False),
        sa.Column("views", sa.Integer(), nullable=True),
        sa.Column("likes", sa.Integer(), nullable=True),
        sa.Column("comments", sa.Integer(), nullable=True),
        sa.Column("thumb_url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("signals", sa.JSON(), nullable=True),
        sa.Column("stats_at", sa.DateTime(), nullable=False),
        sa.Column("found_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["creator_id"], ["creators.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_creator_videos_creator_id", "creator_videos", ["creator_id"])
    op.create_index("ix_creator_videos_status", "creator_videos", ["status"])
    op.create_index("ix_creator_videos_creator_external", "creator_videos", ["creator_id", "external_id"], unique=True)


def downgrade() -> None:
    op.drop_table("creator_videos")
    op.drop_table("creators")
