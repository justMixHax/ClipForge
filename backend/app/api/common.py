"""Shared helpers for the API routes: loading rows, and turning rows into response bodies."""
from __future__ import annotations

from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..analysis.describe import cover_words, credit_line, post_caption
from ..errors import NotFound
from ..jobs import queue
from ..models import Clip, Feedback, Job, Project, Video
from ..schemas import ClipOut, JobOut, VideoOut
from ..services import storage


def content_disposition(filename: str) -> str:
    """A download header that is valid for any file name.

    Header values must be plain Latin-1, and clip titles are not (curly quotes, dashes, emoji), so the
    header carries an ASCII stand-in for old clients plus the real name in the standard UTF-8 form
    (RFC 6266). Browsers use the real name."""
    plain = filename.encode("ascii", "replace").decode("ascii").replace("?", "_").replace('"', "'").replace("\\", "_")
    header = f'attachment; filename="{plain}"'
    if plain != filename:
        header += f"; filename*=UTF-8''{quote(filename, safe='')}"
    return header


def get_video(session: Session, video_id: int) -> Video:
    video = session.get(Video, video_id)
    if video is None:
        raise NotFound("That video does not exist (it may have been deleted)")
    return video


def ensure_project(session: Session, project_id: int | None) -> None:
    """Refuse a project that does not exist with a readable message (rather than a database error)."""
    if project_id is not None and session.get(Project, project_id) is None:
        raise NotFound("That project does not exist (it may have been deleted)")


def get_clip(session: Session, clip_id: int) -> Clip:
    clip = session.get(Clip, clip_id)
    if clip is None:
        raise NotFound("That clip does not exist (it may have been deleted)")
    return clip


def job_out(job: Job) -> JobOut:
    return JobOut.model_validate(job)


def video_out(session: Session, video: Video, duplicate_of: int | None = None) -> VideoOut:
    out = VideoOut.model_validate(video)
    out.stream_url = f"/api/videos/{video.id}/stream"
    out.thumb_url = f"/api/videos/{video.id}/thumbnail" if video.thumb_path else ""
    out.clip_count = session.scalar(select(func.count(Clip.id)).where(Clip.video_id == video.id)) or 0
    active = session.scalars(select(Job).where(Job.video_id == video.id, Job.type == "analyze",
                                               Job.status.in_(queue.ACTIVE)).order_by(Job.id.desc())).first()
    out.active_job = job_out(active) if active else None
    if duplicate_of is None and video.fingerprint:
        duplicate_of = session.scalar(select(Video.id).where(Video.fingerprint == video.fingerprint, Video.id < video.id)
                                      .order_by(Video.id).limit(1))
    out.duplicate_of = duplicate_of
    return out


def clip_out(session: Session, clip: Clip, video: Video | None = None) -> ClipOut:
    # built from the table columns only: "feedback" in the response is a list of labels, not the relationship
    out = ClipOut.model_validate({column.name: getattr(clip, column.name) for column in Clip.__table__.columns})
    video = video or session.get(Video, clip.video_id)
    version = int(clip.rendered_at.timestamp()) if clip.rendered_at else 0
    if clip.file_path:
        out.file_url = f"/api/clips/{clip.id}/file?v={version}"
        out.thumb_url = f"/api/clips/{clip.id}/thumbnail?v={version}" if clip.thumb_path else ""
    if clip.cover_path:
        try:
            made = storage.data_path(clip.cover_path).stat().st_mtime_ns // 1_000_000
            out.cover_url = f"/api/clips/{clip.id}/cover?v={made}"
        except OSError:
            pass  # the file is gone: no thumbnail to show
    if not clip.cover_text:
        out.cover_text, out.cover_word = cover_words(clip.title)
    if video is not None:
        out.video_title, out.creator_name, out.permission_status = video.title, video.creator_name, video.permission_status
        out.post_caption = post_caption(clip.description or clip.title,
                                        credit_line(video.attribution, video.creator_name), clip.hashtags or [])
    if clip.tiktok_caption:
        out.post_caption = clip.tiktok_caption
    # the clip as it was before the prompt edit stays here, for undo
    out.last_edit = {k: v for k, v in clip.last_edit.items() if k != "before"} if clip.last_edit else None
    out.feedback = list(session.scalars(select(Feedback.label).where(Feedback.clip_id == clip.id)
                                        .order_by(Feedback.id)))
    return out
