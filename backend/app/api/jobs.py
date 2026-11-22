"""The processing queue: what is waiting, what is running, cancel and retry."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import ClipForgeError, NotFound
from ..jobs import queue
from ..models import Clip, Job, Video
from ..schemas import JobOut
from .common import job_out

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _get(session: Session, job_id: int) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise NotFound("That job does not exist")
    return job


@router.get("", response_model=list[JobOut])
def list_jobs(active: bool = False, video_id: int | None = None, clip_id: int | None = None,
              limit: int = Query(50, ge=1, le=200), sort: str = Query("queue", pattern="^(queue|recent)$"),
              session: Session = Depends(get_db)) -> list[JobOut]:
    query = select(Job)
    if active:
        query = query.where(Job.status.in_(queue.ACTIVE))
    if video_id is not None:
        query = query.where(Job.video_id == video_id)
    if clip_id is not None:
        query = query.where(Job.clip_id == clip_id)
    # running first, then waiting in the order they will run, then the most recent finished ones
    order = (Job.status != "running", Job.status != "queued", Job.priority.desc(), Job.id.desc()) if not active else (
        Job.status != "running", Job.priority.desc(), Job.id.asc())
    if sort == "recent":  # the work log: finished jobs only, the most recently finished first
        query = query.where(Job.status.in_(("succeeded", "failed", "cancelled")))
        order = (func.coalesce(Job.finished_at, Job.created_at).desc(), Job.id.desc())
    return [job_out(j) for j in session.scalars(query.order_by(*order).limit(limit))]


@router.get("/{job_id}", response_model=JobOut)
def read_job(job_id: int, session: Session = Depends(get_db)) -> JobOut:
    return job_out(_get(session, job_id))


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: int, session: Session = Depends(get_db)) -> JobOut:
    """A waiting job is cancelled at once. A running job stops at its next check (within about a second)."""
    job = _get(session, job_id)
    was_queued = job.status == "queued"
    queue.request_cancel(session, job_id)
    if was_queued:  # nothing will run to put the video or clip back in a usable state, so do it here
        if job.type == "render" and job.clip_id and (clip := session.get(Clip, job.clip_id)) is not None:
            clip.render_status = "ready" if clip.file_path else "none"
        if job.type == "analyze" and job.video_id and (video := session.get(Video, job.video_id)) is not None:
            if video.status == "analyzing":
                video.status = "analyzed" if video.analysis else "ready"
        session.commit()
    session.refresh(job)
    return job_out(job)


@router.post("/{job_id}/retry", response_model=JobOut)
def retry_job(job_id: int, session: Session = Depends(get_db)) -> JobOut:
    """Put a failed or cancelled job back in the queue. Work it already finished is reused."""
    job = _get(session, job_id)
    if job.status not in ("failed", "cancelled"):
        raise ClipForgeError("Only failed or cancelled jobs can be retried")
    if job.type == "render" and job.clip_id is None:
        raise ClipForgeError("The clip this job belonged to was deleted")
    if job.type == "analyze" and job.video_id is None:
        raise ClipForgeError("The video this job belonged to was deleted")
    queue.retry(session, job_id)
    if job.type == "render" and (clip := session.get(Clip, job.clip_id)) is not None:
        clip.render_status, clip.render_error = "queued", ""
        session.commit()
    session.refresh(job)
    return job_out(job)
