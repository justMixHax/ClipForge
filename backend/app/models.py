"""Database tables.

File paths are stored relative to the data directory. Status columns hold plain strings; the allowed
values are listed next to each column and in schemas.py.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, UTCDateTime, utcnow

# --- allowed values ----------------------------------------------------------------------------------
PERMISSION_STATUSES = ("owned", "licensed", "permission", "unknown", "denied")
VIDEO_STATUSES = ("ready", "analyzing", "analyzed", "failed")
JOB_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")
REVIEW_STATUSES = ("pending", "approved", "rejected")
RENDER_STATUSES = ("none", "queued", "rendering", "ready", "failed")
FEEDBACK_LABELS = ("good", "bad", "wrong_moment", "poor_subtitles", "bad_cropping", "not_entertaining",
                   "too_much_context")


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    videos: Mapped[list[Video]] = relationship(back_populates="project")


class Video(Base):
    """A source video: uploaded into the data folder, or linked in place on disk."""

    __tablename__ = "videos"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    original_filename: Mapped[str] = mapped_column(String(300), default="")
    storage_key: Mapped[str] = mapped_column(String(64), unique=True)
    # "managed": the file lives in data/uploads and is deleted with the record.
    # "linked": the file stays where the user keeps it and is never deleted by the app.
    storage_mode: Mapped[str] = mapped_column(String(16), default="managed")
    file_path: Mapped[str] = mapped_column(Text)  # relative to data dir (managed) or absolute (linked)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    fingerprint: Mapped[str] = mapped_column(String(64), default="", index=True)

    duration: Mapped[float] = mapped_column(Float, default=0.0)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    fps: Mapped[float] = mapped_column(Float, default=0.0)
    video_codec: Mapped[str] = mapped_column(String(40), default="")
    audio_codec: Mapped[str] = mapped_column(String(40), default="")
    has_audio: Mapped[bool] = mapped_column(Boolean, default=True)

    # where it came from, and whether clipping it is allowed
    source_type: Mapped[str] = mapped_column(String(16), default="upload")  # upload | path | url
    source_url: Mapped[str] = mapped_column(Text, default="")
    creator_name: Mapped[str] = mapped_column(String(200), default="")
    creator_handle: Mapped[str] = mapped_column(String(200), default="")
    permission_status: Mapped[str] = mapped_column(String(16), default="unknown")
    permission_note: Mapped[str] = mapped_column(Text, default="")
    attribution: Mapped[str] = mapped_column(String(300), default="")

    status: Mapped[str] = mapped_column(String(16), default="ready", index=True)
    language: Mapped[str] = mapped_column(String(16), default="")
    content_preset: Mapped[str] = mapped_column(String(32), default="")
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    thumb_path: Mapped[str] = mapped_column(Text, default="")
    last_error: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    project: Mapped[Project | None] = relationship(back_populates="videos")
    clips: Mapped[list[Clip]] = relationship(back_populates="video", cascade="all, delete-orphan",
                                              passive_deletes=True)
    segments: Mapped[list[TranscriptSegment]] = relationship(back_populates="video",
                                                             cascade="all, delete-orphan", passive_deletes=True)


class TranscriptSegment(Base):
    """One sentence of a video's transcript, with its words. Editable; mirrored into an FTS5 index."""

    __tablename__ = "transcript_segments"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"))
    idx: Mapped[int] = mapped_column(Integer)
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)
    speaker: Mapped[str] = mapped_column(String(40), default="")
    # [[word, start, end, probability], ...]
    words: Mapped[list[list[Any]]] = mapped_column(JSON, default=list)
    edited: Mapped[bool] = mapped_column(Boolean, default=False)

    video: Mapped[Video] = relationship(back_populates="segments")

    __table_args__ = (Index("ix_segments_video_start", "video_id", "start"),)


class Job(Base):
    """A unit of background work. The table is the queue: it survives restarts."""

    __tablename__ = "jobs"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(32))  # analyze | render
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    stage: Mapped[str] = mapped_column(String(64), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    # [{"key","label","status","progress","seconds"}] for the step-by-step display
    stages: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, default=None)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    error: Mapped[str] = mapped_column(Text, default="")
    error_detail: Mapped[str] = mapped_column(Text, default="")
    video_id: Mapped[int | None] = mapped_column(ForeignKey("videos.id", ondelete="SET NULL"), index=True)
    clip_id: Mapped[int | None] = mapped_column(ForeignKey("clips.id", ondelete="SET NULL"), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    worker_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)


class Clip(Base):
    """A suggested or user-made clip. Always linked to its source video and source timestamps."""

    __tablename__ = "clips"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    storage_key: Mapped[str] = mapped_column(String(64), unique=True)
    origin: Mapped[str] = mapped_column(String(16), default="ai")  # ai | manual | variant

    title: Mapped[str] = mapped_column(String(300), default="")
    hook_text: Mapped[str] = mapped_column(String(300), default="")
    hook_options: Mapped[list[str]] = mapped_column(JSON, default=list)
    description: Mapped[str] = mapped_column(Text, default="")
    hashtags: Mapped[list[str]] = mapped_column(JSON, default=list)
    category: Mapped[str] = mapped_column(String(40), default="")
    transcript_text: Mapped[str] = mapped_column(Text, default="")

    # source timestamps in seconds
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)

    # explainable score
    score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    subscores: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    explanation: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    analysis_source: Mapped[str] = mapped_column(String(32), default="")  # heuristic | heuristic+llm | manual
    rank: Mapped[int] = mapped_column(Integer, default=0)
    manual_rank: Mapped[int | None] = mapped_column(Integer, default=None)

    review_status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)

    # edit decisions: aspect, layout, crop, caption style, audio options...
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # edited caption words in clip time; None means "derive from the transcript"
    captions: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, default=None)

    render_status: Mapped[str] = mapped_column(String(16), default="none", index=True)
    render_error: Mapped[str] = mapped_column(Text, default="")
    file_path: Mapped[str] = mapped_column(Text, default="")
    thumb_path: Mapped[str] = mapped_column(Text, default="")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    # the thumbnail: the words on it (empty: from the title), the one in colour, the source time of its picture,
    # and the file
    cover_text: Mapped[str] = mapped_column(String(120), default="")
    cover_word: Mapped[str] = mapped_column(String(60), default="")
    cover_time: Mapped[float | None] = mapped_column(Float, default=None)
    cover_path: Mapped[str] = mapped_column(Text, default="")
    tiktok_caption: Mapped[str] = mapped_column(Text, default="")  # your own caption; empty: the automatic one
    # the last prompt edit: what you asked for, what changed, what could not be done, and the clip before it (undo)
    last_edit: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    out_width: Mapped[int] = mapped_column(Integer, default=0)
    out_height: Mapped[int] = mapped_column(Integer, default=0)
    out_duration: Mapped[float] = mapped_column(Float, default=0.0)
    render_info: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    qc: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, default=None)
    rendered_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)

    variant_of: Mapped[int | None] = mapped_column(ForeignKey("clips.id", ondelete="SET NULL"))
    variant_label: Mapped[str] = mapped_column(String(80), default="")

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    video: Mapped[Video] = relationship(back_populates="clips")
    feedback: Mapped[list[Feedback]] = relationship(back_populates="clip", cascade="all, delete-orphan",
                                                    passive_deletes=True)


class Feedback(Base):
    """What the user thought of a clip. Feeds the ranking weights (see analysis/feedback.py)."""

    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    clip_id: Mapped[int | None] = mapped_column(ForeignKey("clips.id", ondelete="SET NULL"), index=True)
    label: Mapped[str] = mapped_column(String(32))
    note: Mapped[str] = mapped_column(Text, default="")
    # category, sub-scores and edit settings at the time, kept even if the clip is deleted later
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    clip: Mapped[Clip | None] = relationship(back_populates="feedback")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Secret(Base):
    """Encrypted credentials (API keys, OAuth tokens). Values never leave the backend."""

    __tablename__ = "secrets"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Preset(Base):
    """A saved workflow preset (the built-in ones live in code, see analysis/presets.py)."""

    __tablename__ = "presets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))  # clips_ready | render_done | job_failed | info
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str] = mapped_column(String(300), default="")
    read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# --- publishing --------------------------------------------------------------------------------------
PLATFORMS = ("youtube", "tiktok", "instagram")
# scheduled: waiting for its time (or to be uploaded) · publishing: ClipForge is uploading it
# processing: the platform accepted the file and is still working on it · published: the platform confirmed it is live
# scheduled_on_platform: uploaded, and the platform itself publishes it at the chosen time (YouTube)
# private: uploaded and confirmed, visible only to you · in_drafts: waiting in your TikTok inbox for you to post
# failed · cancelled
POST_STATUSES = ("scheduled", "publishing", "processing", "published", "scheduled_on_platform", "private", "in_drafts",
                 "failed", "cancelled")


class Account(Base):
    """A social account connected through the platform's own sign-in. Its tokens are kept encrypted in secrets
    (name "publish.account.<id>") and never leave the backend."""

    __tablename__ = "accounts"
    __table_args__ = (Index("ix_accounts_platform_external", "platform", "external_id", unique=True),
                      {"sqlite_autoincrement": True})

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(16))
    external_id: Mapped[str] = mapped_column(String(100))  # channel id, TikTok open_id, Instagram user id
    name: Mapped[str] = mapped_column(String(200), default="")
    handle: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(20), default="connected")  # connected | needs_reconnect
    detail: Mapped[str] = mapped_column(Text, default="")
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    reconnect_by: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)  # when the sign-in runs out
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    connected_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Post(Base):
    """One clip going to one account. It only counts as published once the platform has confirmed it."""

    __tablename__ = "posts"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(primary_key=True)
    clip_id: Mapped[int | None] = mapped_column(ForeignKey("clips.id", ondelete="SET NULL"), index=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), index=True)
    platform: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24), default="scheduled", index=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)  # when it should go live
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None, index=True)  # when ClipForge acts next
    title: Mapped[str] = mapped_column(String(300), default="")
    caption: Mapped[str] = mapped_column(Text, default="")
    hashtags: Mapped[list[str]] = mapped_column(JSON, default=list)
    privacy: Mapped[str] = mapped_column(String(32), default="")
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    external_id: Mapped[str] = mapped_column(String(200), default="")
    url: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    clip_title: Mapped[str] = mapped_column(String(300), default="")      # kept if the clip is deleted later
    account_name: Mapped[str] = mapped_column(String(200), default="")    # kept if the account is disconnected
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
