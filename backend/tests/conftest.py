"""Shared fixtures. Every test gets its own temporary data directory and database."""
from __future__ import annotations

import http.server
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

# The user's own .env (their real configuration) must never change how the tests behave.
os.environ["CLIPFORGE_ENV_FILE"] = ""

BACKEND = Path(__file__).resolve().parent.parent
ROOT = BACKEND.parent
SAMPLE_VIDEO = ROOT / "samples" / "clipforge_sample.mp4"
SAMPLE_TRUTH = ROOT / "samples" / "clipforge_sample.truth.json"

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def pytest_collection_modifyitems(config, items):  # noqa: ANN001
    skip_ffmpeg = pytest.mark.skip(reason="ffmpeg/ffprobe not on PATH")
    for item in items:
        if "ffmpeg" in item.keywords and not HAVE_FFMPEG:
            item.add_marker(skip_ffmpeg)


@pytest.fixture(scope="session")
def template_db(tmp_path_factory) -> Path:  # noqa: ANN001
    """Run the real migrations once; each test copies the resulting empty database."""
    from app import db

    folder = tmp_path_factory.mktemp("template")
    db.run_migrations(folder / "clipforge.db", folder)
    return folder / "clipforge.db"


@pytest.fixture()
def data_dir(tmp_path: Path, template_db: Path, monkeypatch) -> Path:  # noqa: ANN001
    from app import config, db

    folder = tmp_path / "data"
    folder.mkdir()
    shutil.copyfile(template_db, folder / "clipforge.db")
    monkeypatch.setenv("CLIPFORGE_DATA_DIR", str(folder))
    monkeypatch.setenv("CLIPFORGE_SPAWN_WORKER", "0")
    config.reset_settings_cache()
    config.get_settings().ensure_dirs()
    # A fixed CPU-only hardware report: tests do not probe the real GPU, and renders use x264 everywhere.
    (folder / "hardware.json").write_text(json.dumps({
        "detected_at": "test", "cpu": {"name": "Test CPU", "cores": 4, "threads": 8},
        "memory": {"total_gb": 16.0, "available_gb": 8.0, "used_percent": 50.0}, "gpus": [], "ffmpeg": "test",
        "encoders": {"libx264": {"ok": True, "detail": ""}, "h264_nvenc": {"ok": False, "detail": "not tested"}},
        "whisper_cuda": {"ok": False, "detail": "not tested"}, "face_detector": False,
        "storage": {"path": str(folder), "free_gb": 100.0, "total_gb": 500.0}}), encoding="utf-8")
    db.configure(folder / "clipforge.db")
    yield folder
    db.get_engine().dispose()
    config.reset_settings_cache()


@pytest.fixture()
def session(data_dir: Path):  # noqa: ANN001
    from app import db

    s = db.new_session()
    yield s
    s.close()


def make_test_video(path: Path, seconds: float = 3.0, size: str = "640x360", with_audio: bool = True,
                    source: str = "testsrc2") -> Path:
    """A tiny real video made by FFmpeg, for tests that need actual media."""
    joiner = ":" if "=" in source else "="  # "color=c=black" already has options, "testsrc2" has none
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-t", str(seconds), "-i", f"{source}{joiner}s={size}:r=30"]
    if with_audio:
        cmd += ["-f", "lavfi", "-t", str(seconds), "-i", "sine=frequency=440:sample_rate=48000"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if with_audio:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd += [str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


@pytest.fixture()
def tiny_video(tmp_path: Path) -> Path:
    return make_test_video(tmp_path / "tiny.mp4")


@pytest.fixture(scope="session")
def sample_video() -> Path:
    if not SAMPLE_VIDEO.exists():
        pytest.skip("sample video missing; run scripts/sample/make_sample.py")
    return SAMPLE_VIDEO


# ------------------------------------------------------------- a web site on this PC, for link imports
class LocalSite:
    """A web server on 127.0.0.1 that serves one video, one page without a video, and 404 for the rest."""

    def __init__(self, video: bytes, delay: float = 0.0) -> None:
        site = self
        self.video, self.delay, self.requests = video, delay, []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # noqa: ANN002  (keep the test output quiet)
                pass

            def do_HEAD(self) -> None:  # noqa: N802
                self._serve(send_body=False)

            def do_GET(self) -> None:  # noqa: N802
                self._serve(send_body=True)

            def _serve(self, send_body: bool) -> None:
                path = self.path.split("?")[0]
                site.requests.append((self.command, path))
                if path == "/clip.mp4":
                    body, kind = site.video, "video/mp4"
                elif path == "/page.html":
                    body, kind = b"<html><head><title>Nothing</title></head><body><p>No video here.</p></body></html>", "text/html"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if not send_body:
                    return
                try:
                    for start in range(0, len(body), 8192):
                        self.wfile.write(body[start:start + 8192])
                        if site.delay:
                            time.sleep(site.delay)
                except OSError:
                    pass  # the client hung up (a cancelled download)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}{path}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture(scope="module")
def video_bytes(tmp_path_factory) -> bytes:  # noqa: ANN001
    return make_test_video(tmp_path_factory.mktemp("site") / "clip.mp4", seconds=3).read_bytes()


@pytest.fixture()
def site(video_bytes: bytes):
    local = LocalSite(video_bytes)
    yield local
    local.close()


@pytest.fixture()
def slow_site(video_bytes: bytes):
    local = LocalSite(video_bytes, delay=0.05)
    yield local
    local.close()


@pytest.fixture(autouse=True)
def _claude_off_unless_a_test_turns_it_on(monkeypatch):  # noqa: ANN001
    """Tests must never call the real Claude (it needs a sign-in and uses the plan): the local rules are the
    default engine in tests, and tests of the Claude path switch it on and stand in for the tool."""
    from app.services import settings_store

    monkeypatch.setitem(settings_store.SCHEMA, "ai.engine", ("local", settings_store.SCHEMA["ai.engine"][1]))

