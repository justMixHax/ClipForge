from __future__ import annotations

from datetime import timedelta

from app.db import utcnow
from app.jobs import queue
from app.models import Job


def test_claim_returns_none_when_the_queue_is_empty(session):
    assert queue.claim_next(session, "w1") is None


def test_jobs_are_claimed_highest_priority_first_then_oldest_first(session):
    low_old = queue.enqueue(session, "render", {"n": 1}, priority=0)
    low_new = queue.enqueue(session, "render", {"n": 2}, priority=0)
    high = queue.enqueue(session, "render", {"n": 3}, priority=5)

    order = [queue.claim_next(session, "w1").id for _ in range(3)]

    assert order == [high.id, low_old.id, low_new.id]


def test_claiming_marks_the_job_running_and_counts_the_attempt(session):
    job = queue.enqueue(session, "analyze", {})
    claimed = queue.claim_next(session, "worker-7")

    assert claimed.id == job.id
    assert claimed.status == "running"
    assert claimed.worker_id == "worker-7"
    assert claimed.attempts == 1
    assert claimed.started_at is not None and claimed.heartbeat_at is not None


def test_a_claimed_job_is_not_handed_out_twice(session):
    queue.enqueue(session, "analyze", {})
    assert queue.claim_next(session, "w1") is not None
    assert queue.claim_next(session, "w2") is None


def test_cancelling_a_queued_job_cancels_it_immediately(session):
    job = queue.enqueue(session, "analyze", {})
    queue.request_cancel(session, job.id)

    assert session.get(Job, job.id).status == "cancelled"
    assert queue.claim_next(session, "w1") is None


def test_cancelling_a_running_job_raises_the_flag_for_the_worker(session):
    job = queue.enqueue(session, "analyze", {})
    queue.claim_next(session, "w1")
    queue.request_cancel(session, job.id)

    stored = session.get(Job, job.id)
    assert stored.status == "running"
    assert queue.is_cancel_requested(session, job.id) is True


def test_finish_stores_the_result_and_full_progress(session):
    job = queue.enqueue(session, "analyze", {})
    queue.claim_next(session, "w1")
    queue.finish(session, job.id, {"clips": 4})

    stored = session.get(Job, job.id)
    assert (stored.status, stored.progress, stored.result) == ("succeeded", 1.0, {"clips": 4})
    assert stored.finished_at is not None


def test_fail_stores_a_readable_error_and_the_detail(session):
    job = queue.enqueue(session, "render", {})
    queue.claim_next(session, "w1")
    queue.fail(session, job.id, "FFmpeg could not read the file", "Traceback ...")

    stored = session.get(Job, job.id)
    assert stored.status == "failed"
    assert stored.error == "FFmpeg could not read the file"
    assert stored.error_detail == "Traceback ..."


def test_running_jobs_with_a_stale_heartbeat_go_back_to_the_queue(session):
    job = queue.enqueue(session, "analyze", {})
    queue.claim_next(session, "dead-worker")
    stored = session.get(Job, job.id)
    stored.heartbeat_at = utcnow() - timedelta(minutes=10)
    session.commit()

    assert queue.requeue_stale(session, stale_after_seconds=90) == 1
    again = queue.claim_next(session, "new-worker")
    assert again.id == job.id and again.attempts == 2


def test_running_jobs_with_a_fresh_heartbeat_are_left_alone(session):
    queue.enqueue(session, "analyze", {})
    queue.claim_next(session, "w1")
    assert queue.requeue_stale(session, stale_after_seconds=90) == 0


def test_a_job_that_keeps_crashing_its_worker_is_failed_instead_of_looping(session):
    job = queue.enqueue(session, "analyze", {})
    for _ in range(queue.MAX_ATTEMPTS):
        queue.claim_next(session, "w")
        stored = session.get(Job, job.id)
        stored.heartbeat_at = utcnow() - timedelta(minutes=10)
        session.commit()
        queue.requeue_stale(session, stale_after_seconds=90)

    stored = session.get(Job, job.id)
    assert stored.status == "failed"
    assert "stopped unexpectedly" in stored.error


def test_retry_puts_a_failed_job_back_in_the_queue_with_a_clean_slate(session):
    job = queue.enqueue(session, "render", {})
    queue.claim_next(session, "w1")
    queue.fail(session, job.id, "boom")

    queue.retry(session, job.id)

    stored = session.get(Job, job.id)
    assert (stored.status, stored.error, stored.progress, stored.cancel_requested) == ("queued", "", 0.0, False)


def test_progress_is_stored_clamped_and_refreshes_the_heartbeat(session):
    job = queue.enqueue(session, "analyze", {})
    queue.claim_next(session, "w1")
    stored = session.get(Job, job.id)
    stored.heartbeat_at = utcnow() - timedelta(minutes=5)
    session.commit()

    queue.update_progress(session, job.id, progress=1.7, stage="transcribe", message="12 of 40 minutes")

    session.refresh(stored)
    assert (stored.progress, stored.stage, stored.message) == (1.0, "transcribe", "12 of 40 minutes")
    assert utcnow() - stored.heartbeat_at < timedelta(seconds=30)


def test_enqueueing_the_same_work_twice_returns_the_job_already_waiting(session):
    first = queue.enqueue(session, "analyze", {}, target=("video", 12))
    second = queue.enqueue(session, "analyze", {}, target=("video", 12))
    other = queue.enqueue(session, "analyze", {}, target=("video", 13))

    assert second.id == first.id
    assert other.id != first.id
    assert session.query(Job).count() == 2
