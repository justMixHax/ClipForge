"""Starts the processing worker alongside the web server and keeps it running.

The worker is a separate process (see jobs/worker.py). If it exits unexpectedly it is started again,
with a pause that grows if it keeps failing. On shutdown it is asked to stop through a stop file, which
lets the job it is running go back to the queue cleanly; only if it does not exit in time is it killed.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from ..config import get_settings

log = logging.getLogger("clipforge.supervisor")


class WorkerSupervisor:
    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self.restarts = 0

    def _spawn(self) -> subprocess.Popen:
        backend_dir = Path(__file__).resolve().parents[2]
        return subprocess.Popen([sys.executable, "-m", "app.jobs.worker", "--parent-pid", str(os.getpid())],
                                cwd=backend_dir)

    def _run(self) -> None:
        delay = 2.0
        while not self._stopping.is_set():
            started = time.monotonic()
            try:
                self._process = self._spawn()
            except OSError:
                log.error("could not start the worker", exc_info=True)
                self._stopping.wait(10)
                continue
            log.info("worker process started", extra={"pid": self._process.pid})
            while self._process.poll() is None and not self._stopping.is_set():
                self._stopping.wait(1.0)
            if self._stopping.is_set():
                return
            self.restarts += 1
            delay = 2.0 if time.monotonic() - started > 60 else min(delay * 2, 60.0)
            log.warning("worker exited (code %s); restarting in %.0f s", self._process.returncode, delay)
            self._stopping.wait(delay)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="worker-supervisor", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 12.0) -> None:
        self._stopping.set()
        process = self._process
        if process is None or process.poll() is not None:
            return
        flag = get_settings().data_dir / "worker.stop"
        try:
            flag.write_text("stop", encoding="utf-8")
            process.wait(timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            log.warning("worker did not stop in time; ending it")
            process.kill()
        finally:
            flag.unlink(missing_ok=True)

    def status(self) -> dict:
        running = self._process is not None and self._process.poll() is None
        return {"managed": True, "running": running, "pid": self._process.pid if running else None,
                "restarts": self.restarts}
