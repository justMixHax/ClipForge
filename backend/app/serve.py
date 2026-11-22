"""Starts the web server on the address and port in the settings.

Run from the backend folder:   python -m app.serve
scripts/start.ps1 does this. Keeping the address in one place (the settings) means the server cannot end
up listening somewhere the safety checks do not know about.

Stopping: Ctrl+C in the window it runs in, or scripts/stop.ps1 from anywhere. The script cannot press
Ctrl+C in another window, so it leaves a file (data/server.stop) that this process watches for. Either
way the server shuts down in order: it stops taking requests, then asks the worker to put its job back
in the queue.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from .config import check_bind, get_settings

STOP_FILE = "server.stop"
STOP_GRACE_SECONDS = 4


def server_options() -> dict[str, Any]:
    """Where to listen. Raises SystemExit for an address beyond this PC that was not explicitly allowed."""
    settings = get_settings()
    check_bind(settings)
    # When asked to stop, requests still in flight get a few seconds to finish and are then dropped. Without
    # this limit a browser tab left open (a half-loaded video, an unfinished upload) keeps the server alive
    # for ever, and the worker is never told to put its job back in the queue.
    return {"host": settings.host, "port": settings.port, "log_level": "warning", "factory": True,
            "access_log": False, "server_header": False, "timeout_graceful_shutdown": STOP_GRACE_SECONDS}


def watch_for_stop(server: Any, stop_file: Path, poll: float = 0.5) -> None:
    """Ask the server to shut down once the stop file appears. Returns when the server is stopping."""
    while not server.should_exit:
        if stop_file.exists():
            server.should_exit = True
            return
        time.sleep(poll)


def main() -> None:
    import uvicorn

    options = server_options()
    settings = get_settings()
    settings.ensure_dirs()
    stop_file = settings.data_dir / STOP_FILE
    stop_file.unlink(missing_ok=True)  # a leftover from an earlier run must not stop this one

    server = uvicorn.Server(uvicorn.Config("app.main:create_app", **options))
    threading.Thread(target=watch_for_stop, args=(server, stop_file), name="stop-watch", daemon=True).start()
    shown = "localhost" if options["host"] in ("127.0.0.1", "::1") else options["host"]
    print(f"ClipForge is starting on http://{shown}:{options['port']}   (Ctrl+C to stop)", flush=True)
    try:
        server.run()
    finally:
        stop_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
