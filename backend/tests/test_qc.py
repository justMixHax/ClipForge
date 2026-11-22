"""The export quality checker, run against real files with known defects."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.editing.qc import run_checks, summarize
from tests.conftest import make_test_video

pytestmark = pytest.mark.ffmpeg


def status(checks: list[dict], key: str) -> str:
    return next(c["status"] for c in checks if c["key"] == key)


def check(path: Path, **kw) -> list[dict]:
    args = {"expected_w": 640, "expected_h": 360, "expected_duration": 3.0, "source_has_audio": True}
    return run_checks(path, **{**args, **kw})


def test_a_good_export_passes_every_check(tiny_video: Path):
    checks = check(tiny_video)

    assert {c["key"] for c in checks} >= {"playable", "dimensions", "duration", "audio", "black_frames", "silence",
                                          "subtitles", "dialogue"}
    assert all(c["status"] == "pass" for c in checks), [c for c in checks if c["status"] != "pass"]
    assert summarize(checks) == "pass"


def test_wrong_dimensions_fail(tiny_video: Path):
    checks = check(tiny_video, expected_w=1080, expected_h=1920)
    assert status(checks, "dimensions") == "fail"
    assert summarize(checks) == "fail"


def test_a_clip_much_shorter_than_expected_fails_the_duration_check(tiny_video: Path):
    assert status(check(tiny_video, expected_duration=9.0), "duration") == "fail"


def test_black_footage_is_flagged(tmp_path: Path):
    black = make_test_video(tmp_path / "black.mp4", source="color=c=black")
    assert status(check(black), "black_frames") == "fail"


def test_a_silent_track_is_flagged(tmp_path: Path):
    quiet = tmp_path / "quiet.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-t", "3", "-i", "testsrc2=s=640x360:r=30",
                    "-f", "lavfi", "-t", "3", "-i", "anullsrc=r=48000:cl=stereo", "-c:v", "libx264", "-preset",
                    "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(quiet)], check=True)
    checks = check(quiet)

    assert status(checks, "audio") == "warn"
    assert status(checks, "silence") == "warn"
    assert summarize(checks) == "warn"


def test_missing_audio_fails(tmp_path: Path):
    silent = make_test_video(tmp_path / "noaudio.mp4", with_audio=False)
    assert status(check(silent), "audio") == "fail"


def test_a_damaged_file_fails_the_playable_check(tiny_video: Path, tmp_path: Path):
    broken = tmp_path / "broken.mp4"
    data = tiny_video.read_bytes()
    broken.write_bytes(data[: len(data) // 3])

    checks = check(broken)
    assert status(checks, "playable") == "fail"
    assert summarize(checks) == "fail"


def test_caption_problems_and_clipped_words_are_reported(tiny_video: Path):
    checks = check(tiny_video, caption_report={"overflow": ["A VERY LONG LINE"], "font_found": False,
                                               "font": "Arial Black", "font_requested": "Fancy Font",
                                               "events": 4, "shrunk": 1},
                   cuts_into_word=("start", "there"))

    assert status(checks, "subtitles") == "warn"
    assert status(checks, "dialogue") == "warn"
    detail = next(c["detail"] for c in checks if c["key"] == "subtitles")
    assert "Fancy Font" in detail and "A VERY LONG LINE" in detail
