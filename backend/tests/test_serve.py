"""The server entry point: it listens where the settings say, and nowhere wider without being told to."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import config, serve


def test_the_server_listens_where_the_settings_say(data_dir: Path, monkeypatch):  # noqa: ANN001
    monkeypatch.setenv("CLIPFORGE_PORT", "3111")
    config.reset_settings_cache()

    options = serve.server_options()

    assert (options["host"], options["port"]) == ("127.0.0.1", 3111)


def test_listening_beyond_this_pc_is_refused_unless_switched_on(data_dir: Path, monkeypatch):  # noqa: ANN001
    monkeypatch.setenv("CLIPFORGE_HOST", "0.0.0.0")
    config.reset_settings_cache()
    with pytest.raises(SystemExit) as refused:
        serve.server_options()
    assert "CLIPFORGE_ALLOW_REMOTE" in str(refused.value)

    monkeypatch.setenv("CLIPFORGE_ALLOW_REMOTE", "true")
    config.reset_settings_cache()
    assert serve.server_options()["host"] == "0.0.0.0"


def test_the_app_itself_also_refuses_a_wider_address(data_dir: Path, monkeypatch):  # noqa: ANN001
    """Starting the app some other way (uvicorn directly) must not get around the check."""
    from app.main import create_app

    monkeypatch.setenv("CLIPFORGE_HOST", "192.168.1.20")
    config.reset_settings_cache()
    with pytest.raises(SystemExit):
        create_app()


def test_a_settings_file_can_be_named_or_switched_off(tmp_path: Path, monkeypatch):  # noqa: ANN001
    env_file = tmp_path / "custom.env"
    env_file.write_text("CLIPFORGE_PORT=4555\n", encoding="utf-8")
    monkeypatch.delenv("CLIPFORGE_PORT", raising=False)

    monkeypatch.setenv("CLIPFORGE_ENV_FILE", str(env_file))
    config.reset_settings_cache()
    assert config.get_settings().port == 4555

    monkeypatch.setenv("CLIPFORGE_ENV_FILE", "")          # empty: read no settings file at all
    config.reset_settings_cache()
    assert config.get_settings().port == 3000
    config.reset_settings_cache()


def test_the_tests_do_not_read_the_users_own_settings_file():
    """A .env in the project (the user's real configuration) must never change how the tests behave."""
    import os

    assert os.environ.get("CLIPFORGE_ENV_FILE") == ""


def test_a_stop_file_shuts_the_server_down_cleanly(tmp_path: Path):
    """scripts/stop.ps1 cannot press Ctrl+C in another window, so it leaves a file the server watches for."""
    import threading
    import time

    class FakeServer:
        should_exit = False

    server = FakeServer()
    stop_file = tmp_path / "server.stop"
    watcher = threading.Thread(target=serve.watch_for_stop, args=(server, stop_file, 0.01), daemon=True)
    watcher.start()
    time.sleep(0.05)
    assert server.should_exit is False and watcher.is_alive()      # nothing has been asked yet

    stop_file.write_text("stop", encoding="utf-8")
    watcher.join(timeout=2)

    assert server.should_exit is True and not watcher.is_alive()


def test_the_server_stops_in_order_even_while_a_browser_still_has_a_request_open(data_dir: Path):
    """A browser tab left open keeps connections to the app alive (a half-loaded video, an unfinished
    upload). Stopping must not wait for them for ever: scripts/stop.ps1 gives up and uses force after 20 s."""
    import os
    import socket
    import subprocess
    import sys
    import time
    import urllib.request

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = {**os.environ, "CLIPFORGE_DATA_DIR": str(data_dir), "CLIPFORGE_PORT": str(port),
           "CLIPFORGE_SPAWN_WORKER": "0", "CLIPFORGE_ENV_FILE": ""}
    backend = Path(__file__).resolve().parent.parent
    server = subprocess.Popen([sys.executable, "-m", "app.serve"], cwd=backend, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    client = None
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as response:
                    assert response.status == 200
                break
            except OSError:
                assert server.poll() is None, "the server exited while starting"
                assert time.monotonic() < deadline, "the server did not start"
                time.sleep(0.3)

        # an upload that announces a megabyte, sends a few bytes, and then just sits there
        client = socket.create_connection(("127.0.0.1", port))
        client.sendall(b"POST /api/videos/upload HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Filename: a.mp4\r\n"
                       b"Content-Type: application/octet-stream\r\nContent-Length: 1000000\r\n\r\nabc")
        time.sleep(0.5)

        asked = time.monotonic()
        (data_dir / serve.STOP_FILE).write_text("stop", encoding="utf-8")
        server.wait(timeout=15)                                   # raises if it is still running

        assert time.monotonic() - asked < 15
        assert not (data_dir / serve.STOP_FILE).exists()          # the server tidied its stop request away
    finally:
        if client is not None:
            client.close()
        if server.poll() is None:
            server.kill()
            server.wait(timeout=10)
