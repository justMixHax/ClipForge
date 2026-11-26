from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from app.errors import JobCancelled, MediaError
from app.services import ffmpeg
from tests.conftest import make_test_video

pytestmark = pytest.mark.ffmpeg


def test_probe_reads_the_real_properties_of_a_video(tiny_video: Path):
    info = ffmpeg.probe(tiny_video)

    assert (info.width, info.height) == (640, 360)
    assert info.fps == pytest.approx(30, abs=0.1)
    assert info.duration == pytest.approx(3.0, abs=0.15)
    assert info.video_codec == "h264"
    assert info.has_audio is True and info.audio_codec == "aac"
    assert info.size_bytes == tiny_video.stat().st_size


def test_probe_reports_when_there_is_no_audio_track(tmp_path: Path):
    silent = make_test_video(tmp_path / "silent.mp4", with_audio=False)
    info = ffmpeg.probe(silent)

    assert info.has_audio is False and info.audio_codec == ""


def test_probe_rejects_a_file_that_is_not_media(tmp_path: Path):
    fake = tmp_path / "fake.mp4"
    fake.write_bytes(b"this is definitely not a video" * 100)

    with pytest.raises(MediaError, match="not a video"):
        ffmpeg.probe(fake)


def test_probe_rejects_audio_only_files(tmp_path: Path):
    audio = tmp_path / "audio.mkv"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-t", "1", "-i", "sine=frequency=300",
                    "-c:a", "aac", str(audio)], check=True)

    with pytest.raises(MediaError, match="no video"):
        ffmpeg.probe(audio)


def test_run_reports_rising_progress_up_to_the_end(tiny_video: Path, tmp_path: Path):
    seen: list[float] = []
    out = tmp_path / "out.mp4"

    ffmpeg.run(["-i", str(tiny_video), "-c:v", "libx264", "-preset", "ultrafast", "-an", str(out)],
               duration=3.0, on_progress=seen.append)

    assert out.exists() and out.stat().st_size > 0
    assert seen == sorted(seen)
    assert seen[-1] == pytest.approx(1.0, abs=0.02)


def test_run_raises_a_useful_error_when_ffmpeg_fails(tmp_path: Path):
    with pytest.raises(ffmpeg.FFmpegError) as err:
        ffmpeg.run(["-i", str(tmp_path / "missing.mp4"), str(tmp_path / "out.mp4")])

    assert "missing.mp4" in str(err.value)
    assert err.value.returncode != 0


def test_run_stops_quickly_when_cancelled_and_removes_the_partial_file(tmp_path: Path):
    # Cancel once FFmpeg has reported progress, or after 3 seconds: a slow PC can be late with the first progress
    # of a heavy encode, and what is tested is how fast a cancel lands.
    out = tmp_path / "long.mp4"
    started = time.monotonic()
    progressed: list[float] = []
    asked: list[float] = []

    def cancel_now() -> bool:
        if progressed or time.monotonic() - started > 3:
            asked.append(asked[0] if asked else time.monotonic())
            return True
        return False

    with pytest.raises(JobCancelled):
        ffmpeg.run(["-f", "lavfi", "-t", "600", "-i", "testsrc2=s=1920x1080:r=30", "-c:v", "libx264",
                    "-preset", "medium", str(out)],
                   duration=600.0, on_progress=progressed.append, should_cancel=cancel_now, cleanup=[out])

    assert time.monotonic() - asked[0] < 10
    assert not out.exists()


def test_stopping_a_launcher_also_stops_the_program_it_started():
    # Chocolatey puts a small launcher in front of ffmpeg.exe. Killing only the launcher left the real FFmpeg
    # encoding for minutes, holding the output pipes open, so a cancel took that long to land.
    import sys

    import psutil

    # The real interpreter, not a venv's python.exe: that is itself a launcher, and it stops its child on exit.
    python = getattr(sys, "_base_executable", sys.executable)
    child_code = "import time; time.sleep(120)"
    launcher_code = ("import subprocess, sys; "
                     f"subprocess.Popen([sys.executable, '-c', {child_code!r}]).wait()")
    launcher = subprocess.Popen([python, "-c", launcher_code], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.monotonic() + 15
    while not psutil.Process(launcher.pid).children() and time.monotonic() < deadline:
        time.sleep(0.05)
    (child,) = psutil.Process(launcher.pid).children()

    try:
        ffmpeg._stop(launcher)

        assert launcher.poll() is not None
        child.wait(timeout=5)  # raises psutil.TimeoutExpired while it is still running
    finally:
        if child.is_running():
            child.kill()


def test_gray_frames_have_the_requested_size_and_count(tiny_video: Path):
    frames = list(ffmpeg.iter_frames(tiny_video, fps=2, width=160, height=90, pix_fmt="gray"))

    assert 5 <= len(frames) <= 7  # 3 seconds at 2 frames per second
    assert all(f.shape == (90, 160) for f in frames)


def test_colour_frames_can_be_read_from_a_time_range(tiny_video: Path):
    frames = list(ffmpeg.iter_frames(tiny_video, fps=4, width=320, height=180, pix_fmt="bgr24",
                                     start=1.0, duration=1.0))

    assert 3 <= len(frames) <= 5
    assert frames[0].shape == (180, 320, 3)


def blocks_video(path: Path, count: int = 16, seconds: float = 0.5) -> Path:
    """A video of short blocks, each a different grey, so a frame's grey says when it was on screen."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for i in range(count):
        cmd += ["-f", "lavfi", "-t", str(seconds), "-i", f"color=c=0x{i * 16:02x}{i * 16:02x}{i * 16:02x}:s=64x36:r=30"]
    cmd += ["-filter_complex", "".join(f"[{i}:v]" for i in range(count)) + f"concat=n={count}:v=1:a=0[v]",
            "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def test_frame_k_shows_the_picture_at_the_start_plus_k_intervals_even_at_low_rates(tmp_path: Path):
    video = blocks_video(tmp_path / "blocks.mp4")
    every = [f.mean() for f in ffmpeg.iter_frames(video, fps=30, width=64, height=36)]  # every frame, in order
    grey_of_block = [sum(every[b * 15:(b + 1) * 15]) / 15 for b in range(16)]

    sampled = list(ffmpeg.iter_frames(video, fps=0.5, width=64, height=36, start=0.2, duration=7.5))

    shown = [min(range(16), key=lambda b: abs(grey_of_block[b] - frame.mean())) for frame in sampled]
    assert shown == [int((0.2 + k / 0.5) / 0.5) for k in range(len(sampled))] and len(sampled) >= 3


def test_extract_audio_writes_16k_mono_wav(tiny_video: Path, tmp_path: Path):
    import wave

    wav = tmp_path / "a.wav"
    ffmpeg.extract_audio(tiny_video, wav)

    with wave.open(str(wav), "rb") as wf:
        assert (wf.getframerate(), wf.getnchannels(), wf.getsampwidth()) == (16000, 1, 2)
        assert wf.getnframes() / 16000 == pytest.approx(3.0, abs=0.15)
