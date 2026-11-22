"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-11-21 15:10:44.943257
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('notifications',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('link', sa.String(length=300), nullable=False),
    sa.Column('read', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_notifications_read'), ['read'], unique=False)

    op.create_table('presets',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('config', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('projects',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sqlite_autoincrement=True
    )
    op.create_table('secrets',
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('ciphertext', sa.LargeBinary(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('name')
    )
    op.create_table('settings',
    sa.Column('key', sa.String(length=100), nullable=False),
    sa.Column('value', sa.JSON(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('videos',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=True),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('original_filename', sa.String(length=300), nullable=False),
    sa.Column('storage_key', sa.String(length=64), nullable=False),
    sa.Column('storage_mode', sa.String(length=16), nullable=False),
    sa.Column('file_path', sa.Text(), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('fingerprint', sa.String(length=64), nullable=False),
    sa.Column('duration', sa.Float(), nullable=False),
    sa.Column('width', sa.Integer(), nullable=False),
    sa.Column('height', sa.Integer(), nullable=False),
    sa.Column('fps', sa.Float(), nullable=False),
    sa.Column('video_codec', sa.String(length=40), nullable=False),
    sa.Column('audio_codec', sa.String(length=40), nullable=False),
    sa.Column('has_audio', sa.Boolean(), nullable=False),
    sa.Column('source_type', sa.String(length=16), nullable=False),
    sa.Column('source_url', sa.Text(), nullable=False),
    sa.Column('creator_name', sa.String(length=200), nullable=False),
    sa.Column('creator_handle', sa.String(length=200), nullable=False),
    sa.Column('permission_status', sa.String(length=16), nullable=False),
    sa.Column('permission_note', sa.Text(), nullable=False),
    sa.Column('attribution', sa.String(length=300), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('language', sa.String(length=16), nullable=False),
    sa.Column('content_preset', sa.String(length=32), nullable=False),
    sa.Column('analysis', sa.JSON(), nullable=True),
    sa.Column('thumb_path', sa.Text(), nullable=False),
    sa.Column('last_error', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('storage_key'),
    sqlite_autoincrement=True
    )
    with op.batch_alter_table('videos', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_videos_fingerprint'), ['fingerprint'], unique=False)
        batch_op.create_index(batch_op.f('ix_videos_project_id'), ['project_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_videos_status'), ['status'], unique=False)

    op.create_table('clips',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('video_id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=True),
    sa.Column('storage_key', sa.String(length=64), nullable=False),
    sa.Column('origin', sa.String(length=16), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('hook_text', sa.String(length=300), nullable=False),
    sa.Column('hook_options', sa.JSON(), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('hashtags', sa.JSON(), nullable=False),
    sa.Column('category', sa.String(length=40), nullable=False),
    sa.Column('transcript_text', sa.Text(), nullable=False),
    sa.Column('start', sa.Float(), nullable=False),
    sa.Column('end', sa.Float(), nullable=False),
    sa.Column('score', sa.Float(), nullable=False),
    sa.Column('subscores', sa.JSON(), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('explanation', sa.Text(), nullable=False),
    sa.Column('evidence', sa.JSON(), nullable=False),
    sa.Column('analysis_source', sa.String(length=32), nullable=False),
    sa.Column('rank', sa.Integer(), nullable=False),
    sa.Column('manual_rank', sa.Integer(), nullable=True),
    sa.Column('review_status', sa.String(length=16), nullable=False),
    sa.Column('favorite', sa.Boolean(), nullable=False),
    sa.Column('settings', sa.JSON(), nullable=False),
    sa.Column('captions', sa.JSON(), nullable=True),
    sa.Column('render_status', sa.String(length=16), nullable=False),
    sa.Column('render_error', sa.Text(), nullable=False),
    sa.Column('file_path', sa.Text(), nullable=False),
    sa.Column('thumb_path', sa.Text(), nullable=False),
    sa.Column('file_size', sa.Integer(), nullable=False),
    sa.Column('out_width', sa.Integer(), nullable=False),
    sa.Column('out_height', sa.Integer(), nullable=False),
    sa.Column('out_duration', sa.Float(), nullable=False),
    sa.Column('render_info', sa.JSON(), nullable=True),
    sa.Column('qc', sa.JSON(), nullable=True),
    sa.Column('rendered_at', sa.DateTime(), nullable=True),
    sa.Column('variant_of', sa.Integer(), nullable=True),
    sa.Column('variant_label', sa.String(length=80), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['variant_of'], ['clips.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['video_id'], ['videos.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('storage_key'),
    sqlite_autoincrement=True
    )
    with op.batch_alter_table('clips', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_clips_render_status'), ['render_status'], unique=False)
        batch_op.create_index(batch_op.f('ix_clips_review_status'), ['review_status'], unique=False)
        batch_op.create_index(batch_op.f('ix_clips_score'), ['score'], unique=False)
        batch_op.create_index(batch_op.f('ix_clips_video_id'), ['video_id'], unique=False)

    op.create_table('transcript_segments',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('video_id', sa.Integer(), nullable=False),
    sa.Column('idx', sa.Integer(), nullable=False),
    sa.Column('start', sa.Float(), nullable=False),
    sa.Column('end', sa.Float(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('speaker', sa.String(length=40), nullable=False),
    sa.Column('words', sa.JSON(), nullable=False),
    sa.Column('edited', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['video_id'], ['videos.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('transcript_segments', schema=None) as batch_op:
        batch_op.create_index('ix_segments_video_start', ['video_id', 'start'], unique=False)

    op.create_table('feedback',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('clip_id', sa.Integer(), nullable=True),
    sa.Column('label', sa.String(length=32), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('snapshot', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['clip_id'], ['clips.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('feedback', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_feedback_clip_id'), ['clip_id'], unique=False)

    op.create_table('jobs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('type', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('priority', sa.Integer(), nullable=False),
    sa.Column('progress', sa.Float(), nullable=False),
    sa.Column('stage', sa.String(length=64), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('stages', sa.JSON(), nullable=True),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('result', sa.JSON(), nullable=True),
    sa.Column('error', sa.Text(), nullable=False),
    sa.Column('error_detail', sa.Text(), nullable=False),
    sa.Column('video_id', sa.Integer(), nullable=True),
    sa.Column('clip_id', sa.Integer(), nullable=True),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('cancel_requested', sa.Boolean(), nullable=False),
    sa.Column('worker_id', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=True),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.Column('heartbeat_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['clip_id'], ['clips.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['video_id'], ['videos.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sqlite_autoincrement=True
    )
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_jobs_clip_id'), ['clip_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_jobs_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_jobs_video_id'), ['video_id'], unique=False)

    # Full-text index over transcript sentences ("search inside videos"). External-content FTS5 table
    # kept in step with transcript_segments by triggers.
    op.execute("CREATE VIRTUAL TABLE transcript_fts USING fts5("
               "text, content='transcript_segments', content_rowid='id', tokenize='porter unicode61')")
    op.execute("CREATE TRIGGER transcript_segments_ai AFTER INSERT ON transcript_segments BEGIN "
               "INSERT INTO transcript_fts(rowid, text) VALUES (new.id, new.text); END")
    op.execute("CREATE TRIGGER transcript_segments_ad AFTER DELETE ON transcript_segments BEGIN "
               "INSERT INTO transcript_fts(transcript_fts, rowid, text) VALUES ('delete', old.id, old.text); END")
    op.execute("CREATE TRIGGER transcript_segments_au AFTER UPDATE OF text ON transcript_segments BEGIN "
               "INSERT INTO transcript_fts(transcript_fts, rowid, text) VALUES ('delete', old.id, old.text); "
               "INSERT INTO transcript_fts(rowid, text) VALUES (new.id, new.text); END")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS transcript_segments_au")
    op.execute("DROP TRIGGER IF EXISTS transcript_segments_ad")
    op.execute("DROP TRIGGER IF EXISTS transcript_segments_ai")
    op.execute("DROP TABLE IF EXISTS transcript_fts")
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_jobs_video_id'))
        batch_op.drop_index(batch_op.f('ix_jobs_status'))
        batch_op.drop_index(batch_op.f('ix_jobs_clip_id'))

    op.drop_table('jobs')
    with op.batch_alter_table('feedback', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_feedback_clip_id'))

    op.drop_table('feedback')
    with op.batch_alter_table('transcript_segments', schema=None) as batch_op:
        batch_op.drop_index('ix_segments_video_start')

    op.drop_table('transcript_segments')
    with op.batch_alter_table('clips', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_clips_video_id'))
        batch_op.drop_index(batch_op.f('ix_clips_score'))
        batch_op.drop_index(batch_op.f('ix_clips_review_status'))
        batch_op.drop_index(batch_op.f('ix_clips_render_status'))

    op.drop_table('clips')
    with op.batch_alter_table('videos', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_videos_status'))
        batch_op.drop_index(batch_op.f('ix_videos_project_id'))
        batch_op.drop_index(batch_op.f('ix_videos_fingerprint'))

    op.drop_table('videos')
    op.drop_table('settings')
    op.drop_table('secrets')
    op.drop_table('projects')
    op.drop_table('presets')
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_notifications_read'))

    op.drop_table('notifications')
