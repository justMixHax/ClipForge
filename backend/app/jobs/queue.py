"""The persistent job queue. The jobs table is the queue, so queued and half-finished work survives a
restart. Every function here is one small transaction, so the API and the worker can call them freely.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import Job

ACTIVE = ("queued", "running")
# A job whose worker died this many times is failed rather than retried forever.
MAX_ATTEMPTS = 3


def enqueue(session: Session, type: str, payload: dict[str, Any] | None = None, *, video_id: int | None = None,
            clip_id: int | None = None, priority: int = 0, stages: list[dict[str, Any]] | None = None,
            target: tuple[str, int | str] | None = None) -> Job:
    """Add a job. With a target, asking for the same work while it is already waiting or running returns
    the existing job instead of queueing a duplicate."""
    payload = dict(payload or {})
    if target is not None:
        payload["_target"] = list(target)
        for existing in session.scalars(select(Job).where(Job.type == type, Job.status.in_(ACTIVE))):
            if (existing.payload or {}).get("_target") == payload["_target"]:
                return existing
    job = Job(type=type, payload=payload, video_id=video_id, clip_id=clip_id, priority=priority, stages=stages)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def claim_next(session: Session, worker_id: str) -> Job | None:
    """Take the next job: highest priority first, oldest first within a priority."""
    while True:
        job_id = session.execute(
            select(Job.id).where(Job.status == "queued")
            .order_by(Job.priority.desc(), Job.created_at.asc(), Job.id.asc()).limit(1)
        ).scalar_one_or_none()
        if job_id is None:
            session.rollback()
            return None
        now = utcnow()
        claimed = session.execute(
            update(Job).where(Job.id == job_id, Job.status == "queued")
            .values(status="running", worker_id=worker_id, started_at=now, heartbeat_at=now,
                    attempts=Job.attempts + 1, cancel_requested=False, error="", error_detail="",
                    finished_at=None)
        )
        session.commit()
        if claimed.rowcount == 1:
            job = session.get(Job, job_id)
            session.refresh(job)
            return job


def update_progress(session: Session, job_id: int, *, progress: float | None = None, stage: str | None = None,
                    message: str | None = None, stages: list[dict[str, Any]] | None = None) -> None:
    values: dict[str, Any] = {"heartbeat_at": utcnow()}
    if progress is not None:
        values["progress"] = min(1.0, max(0.0, float(progress)))
    if stage is not None:
        values["stage"] = stage
    if message is not None:
        values["message"] = message
    if stages is not None:
        values["stages"] = stages
    session.execute(update(Job).where(Job.id == job_id).values(**values))
    session.commit()


def heartbeat(session: Session, job_id: int) -> None:
    session.execute(update(Job).where(Job.id == job_id, Job.status == "running").values(heartbeat_at=utcnow()))
    session.commit()


def request_cancel(session: Session, job_id: int) -> Job | None:
    job = session.get(Job, job_id)
    if job is None:
        return None
    if job.status == "queued":
        job.status = "cancelled"
        job.message = "Cancelled before it started"
        job.finished_at = utcnow()
    elif job.status == "running":
        job.cancel_requested = True
        job.message = "Cancelling…"
    session.commit()
    return job


def is_cancel_requested(session: Session, job_id: int) -> bool:
    flag = session.execute(select(Job.cancel_requested).where(Job.id == job_id)).scalar_one_or_none()
    session.rollback()  # end the read transaction so the next check sees fresh data
    return bool(flag)


def finish(session: Session, job_id: int, result: dict[str, Any] | None = None) -> None:
    session.execute(update(Job).where(Job.id == job_id).values(
        status="succeeded", progress=1.0, result=result, finished_at=utcnow(), heartbeat_at=utcnow()))
    session.commit()


def fail(session: Session, job_id: int, error: str, detail: str = "") -> None:
    session.execute(update(Job).where(Job.id == job_id).values(
        status="failed", error=error, error_detail=detail, finished_at=utcnow()))
    session.commit()


def mark_cancelled(session: Session, job_id: int, message: str = "Cancelled") -> None:
    session.execute(update(Job).where(Job.id == job_id).values(
        status="cancelled", message=message, finished_at=utcnow()))
    session.commit()


def requeue_stale(session: Session, stale_after_seconds: float = 90.0) -> int:
    """Recover jobs whose worker stopped without finishing them (crash, power loss, closed window).
    Cached transcripts and analysis are kept, so the re-run skips work that was already done."""
    cutoff = utcnow() - timedelta(seconds=stale_after_seconds)
    requeued = 0
    for job in session.scalars(select(Job).where(Job.status == "running")):
        if job.heartbeat_at is not None and job.heartbeat_at >= cutoff:
            continue
        if job.attempts >= MAX_ATTEMPTS:
            job.status = "failed"
            job.error = (f"The worker stopped unexpectedly {job.attempts} times while running this job. "
                         "Check data/logs/worker.log, then use Retry.")
            job.finished_at = utcnow()
        else:
            job.status = "queued"
            job.worker_id = ""
            job.message = "Recovered after an interruption; resuming"
            requeued += 1
    session.commit()
    return requeued


def retry(session: Session, job_id: int) -> Job | None:
    job = session.get(Job, job_id)
    if job is None or job.status not in ("failed", "cancelled"):
        return job
    job.status = "queued"
    job.error = ""
    job.error_detail = ""
    job.progress = 0.0
    job.stage = ""
    job.message = ""
    job.cancel_requested = False
    job.attempts = 0
    job.result = None
    job.started_at = None
    job.finished_at = None
    session.commit()
    return job
