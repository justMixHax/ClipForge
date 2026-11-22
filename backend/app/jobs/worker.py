"""The processing worker: a separate process that takes jobs from the queue one at a time.

    python -m app.jobs.worker [--parent-pid PID]

It is separate from the web server so a heavy render or a crash in a media library cannot freeze or
kill the app. If it stops for any reason, queued jobs wait and the interrupted job is picked up again
on the next start, reusing whatever it had already finished.
"""
from __future__ import annotations

import argparse
import logging
import os
import socket
import threading
import time

import psutil

from .. import db
from ..config import get_settings
from ..logging_config import setup_logging
from . import pipeline, queue

log = logging.getLogger("clipforge.worker")
HEARTBEAT_SECONDS = 10
STALE_SECONDS = 45


def stop_file() -> "os.PathLike[str]":
    return get_settings().data_dir / "worker.stop"


def _watch(parent_pid: int | None, stop: threading.Event) -> None:
    """Stop when asked to (stop file) or when the app that started this worker is gone."""
    flag = stop_file()
    while not stop.is_set():
        if os.path.exists(flag) or (parent_pid and not psutil.pid_exists(parent_pid)):
            log.info("stop requested; finishing up")
            pipeline.SHUTDOWN.set()
            stop.set()
            return
        time.sleep(1.0)


def _heartbeat(job_id: int, done: threading.Event) -> None:
    while not done.wait(HEARTBEAT_SECONDS):
        try:
            with db.session_scope() as s:
                queue.heartbeat(s, job_id)
        except Exception:  # noqa: BLE001  (a missed heartbeat must not kill the job)
            log.warning("heartbeat failed", exc_info=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ClipForge processing worker")
    parser.add_argument("--parent-pid", type=int, default=None, help="exit when this process is gone")
    args = parser.parse_args(argv)

    settings = get_settings()
    settings.ensure_dirs()
    setup_logging(settings.logs_dir, "worker", settings.log_level)
    db.configure(settings.db_path)
    db.run_migrations(settings.db_path, settings.data_dir)
    try:
        os.remove(stop_file())
    except OSError:
        pass

    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    stop = threading.Event()
    threading.Thread(target=_watch, args=(args.parent_pid, stop), daemon=True).start()
    log.info("worker started", extra={"worker_id": worker_id})

    last_recovery = 0.0
    try:
        while not stop.is_set():
            now = time.monotonic()
            if now - last_recovery > 15:
                last_recovery = now
                with db.session_scope() as s:
                    recovered = queue.requeue_stale(s, STALE_SECONDS)
                if recovered:
                    log.info("recovered interrupted jobs", extra={"count": recovered})
            with db.session_scope() as s:
                job = queue.claim_next(s, worker_id)
                job_id = job.id if job else None
                job_type = job.type if job else ""
            if job_id is None:
                stop.wait(0.5)
                continue
            log.info("job started", extra={"job_id": job_id, "type": job_type})
            done = threading.Event()
            beat = threading.Thread(target=_heartbeat, args=(job_id, done), daemon=True)
            beat.start()
            try:
                pipeline.run_job(job_id)
            finally:
                done.set()
    except KeyboardInterrupt:
        pipeline.SHUTDOWN.set()
    log.info("worker stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
