"""Everything that starts FFmpeg or FFprobe goes through this module.

Commands are always argument lists (never a shell string), so nothing typed in the browser can turn into
a shell command. Long runs report progress, can be cancelled, and raise an error that carries FFmpeg's own
last lines so failures are explainable.
"""
from __future__ import annotations

import json
import queue as _queue
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from ..config import get_settings
from ..errors import DependencyMissing, JobCancelled, MediaError

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console window flashing up on Windows
_EOF = object()


class FFmpegError(RuntimeError):
    def __init__(self, message: str, returncode: int, stderr_tail: str) -> None:
        super().__init__(f"{message}: {stderr_tail}" if stderr_tail else message)
        self.returncode = returncode
        self.stderr_tail = stderr_tail


@dataclass(slots=True)
class MediaInfo:
    duration: float
    width: int
    height: int
    fps: float
    video_codec: str
    audio_codec: str
    has_audio: bool
    size_bytes: int
    rotation: int = 0
    audio_channels: int = 0
    format_name: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _fraction(value: str | None) -> float:
    if not value or value in ("0/0", "N/A"):
        return 0.0
    if "/" in value:
        num, den = value.split("/", 1)
        try:
            return float(num) / float(den) if float(den) else 0.0
        except ValueError:
            return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def _missing(tool: str) -> DependencyMissing:
    return DependencyMissing(f"{tool} was not found on this PC",
                             hint="Install FFmpeg (winget install Gyan.FFmpeg) and restart ClipForge.")


def probe(path: Path) -> MediaInfo:
    """Read a file's real properties. Raises MediaError if it is not a usable video."""
    cmd = [get_settings().ffprobe_path, "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
           str(path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              creationflags=NO_WINDOW, timeout=180)
    except FileNotFoundError as exc:
        raise _missing("FFprobe") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError("Reading this file took too long; it may be damaged") from exc

    try:
        data = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError:
        data = {}
    streams = data.get("streams") or []
    if proc.returncode != 0 or not streams:
        reason = (proc.stderr.strip().splitlines() or ["unrecognised data"])[-1]
        raise MediaError("This file is not a video FFmpeg can read", hint=reason[-200:])

    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    if video is None:
        raise MediaError("This file has no video track (audio only)")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format") or {}

    duration = _fraction(fmt.get("duration")) or _fraction(video.get("duration"))
    if duration <= 0:
        raise MediaError("Could not read this video's length; the file may be incomplete")

    rotation = 0
    for side in video.get("side_data_list") or []:
        if "rotation" in side:
            rotation = int(round(float(side["rotation"])))
    if not rotation and (video.get("tags") or {}).get("rotate"):
        rotation = int((video.get("tags") or {})["rotate"])
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    if abs(rotation) % 180 == 90:  # FFmpeg rotates on decode, so these are the dimensions we will see
        width, height = height, width
    if width <= 0 or height <= 0:
        raise MediaError("This video has no readable picture size")

    return MediaInfo(
        duration=duration, width=width, height=height,
        fps=_fraction(video.get("avg_frame_rate")) or _fraction(video.get("r_frame_rate")),
        video_codec=str(video.get("codec_name") or ""),
        audio_codec=str(audio.get("codec_name") or "") if audio else "",
        has_audio=audio is not None,
        size_bytes=int(float(fmt.get("size") or 0)) or path.stat().st_size,
        rotation=rotation,
        audio_channels=int(audio.get("channels") or 0) if audio else 0,
        format_name=str(fmt.get("format_name") or ""),
    )


def _pump(stream, sink: _queue.Queue) -> None:  # noqa: ANN001
    try:
        for line in stream:
            sink.put(line)
    finally:
        sink.put(_EOF)


def _drain(stream, lines: deque) -> None:  # noqa: ANN001
    for line in stream:
        lines.append(line.rstrip())


def _remove(paths: Sequence[Path]) -> None:
    for p in paths:
        for _ in range(10):  # Windows can hold the file for a moment after the process dies
            try:
                Path(p).unlink(missing_ok=True)
                break
            except PermissionError:
                time.sleep(0.2)


def run(args: Sequence[str], *, duration: float | None = None,
        on_progress: Callable[[float], None] | None = None,
        should_cancel: Callable[[], bool] | None = None, cwd: Path | None = None,
        cleanup: Sequence[Path] = (), loglevel: str = "error") -> str:
    """Run FFmpeg to completion and return what it printed on stderr.

    duration: expected output length in seconds, used to turn FFmpeg's timestamps into 0..1 progress.
    cleanup:  output files to delete if the run fails or is cancelled, so no broken files are left behind.
    """
    cmd = [get_settings().ffmpeg_path, "-hide_banner", "-nostdin", "-y", "-loglevel", loglevel,
           "-progress", "pipe:1", "-nostats", *args]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, text=True,
                                encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    except FileNotFoundError as exc:
        raise _missing("FFmpeg") from exc

    stderr_lines: deque[str] = deque(maxlen=4000)
    lines: _queue.Queue = _queue.Queue()
    readers = [threading.Thread(target=_drain, args=(proc.stderr, stderr_lines), daemon=True),
               threading.Thread(target=_pump, args=(proc.stdout, lines), daemon=True)]
    for t in readers:
        t.start()

    best = 0.0
    cancelled = False
    try:
        while True:
            try:
                line = lines.get(timeout=0.25)
            except _queue.Empty:
                line = None
            if should_cancel is not None and should_cancel():
                cancelled = True
                break
            if line is _EOF:
                break
            if not line or on_progress is None:
                continue
            key, _, value = line.strip().partition("=")
            if key in ("out_time_us", "out_time_ms") and duration and value.lstrip("-").isdigit():
                fraction = min(1.0, max(0.0, int(value) / 1_000_000 / duration))
                if fraction > best:
                    best = fraction
                    on_progress(best)
            elif key == "progress" and value == "end":
                best = 1.0
                on_progress(1.0)
    finally:
        if proc.poll() is None:
            proc.kill()
        returncode = proc.wait()
        for t in readers:
            t.join(timeout=5)
        for stream in (proc.stdout, proc.stderr):
            if stream:
                stream.close()

    if cancelled:
        _remove(cleanup)
        raise JobCancelled()
    stderr_text = "\n".join(stderr_lines)
    if returncode != 0:
        _remove(cleanup)
        tail = " | ".join(line for line in list(stderr_lines)[-6:] if line.strip())
        raise FFmpegError("FFmpeg failed", returncode, tail[-900:])
    return stderr_text


def iter_frames(path: Path, *, fps: float, width: int, height: int, pix_fmt: str = "gray",
                start: float | None = None, duration: float | None = None,
                should_cancel: Callable[[], bool] | None = None) -> Iterator[np.ndarray]:
    """Decode a video into small raw frames. Frame k shows the picture at (start or 0) + k / fps."""
    channels = 1 if pix_fmt == "gray" else 3
    frame_bytes = width * height * channels
    cmd = [get_settings().ffmpeg_path, "-hide_banner", "-nostdin", "-loglevel", "error"]
    if start is not None:
        cmd += ["-ss", f"{max(0.0, start):.3f}"]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-i", str(path), "-an", "-sn", "-dn",
            "-vf", f"fps={fps},scale={width}:{height}:flags=bilinear,format={pix_fmt}", "-f", "rawvideo", "-"]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW,
                                bufsize=frame_bytes * 4)
    except FileNotFoundError as exc:
        raise _missing("FFmpeg") from exc
    try:
        while True:
            buffer = proc.stdout.read(frame_bytes)
            if len(buffer) < frame_bytes:
                break
            if should_cancel is not None and should_cancel():
                raise JobCancelled()
            frame = np.frombuffer(buffer, dtype=np.uint8)
            yield frame.reshape((height, width) if channels == 1 else (height, width, channels))
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.stdout.close()
        proc.wait()


def extract_audio(src: Path, dst: Path, *, sample_rate: int = 16000, duration: float | None = None,
                  on_progress: Callable[[float], None] | None = None,
                  should_cancel: Callable[[], bool] | None = None) -> Path:
    """Write the audio as a mono 16-bit WAV, the format the speech model and audio analysis read."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    run(["-i", str(src), "-vn", "-sn", "-dn", "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(dst)],
        duration=duration, on_progress=on_progress, should_cancel=should_cancel, cleanup=[dst])
    return dst


def grab_frame(src: Path, at: float, dst: Path, *, width: int = 640) -> Path:
    """Save one frame of a video as a JPEG (thumbnails and cover frames come from real footage)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    run(["-ss", f"{max(0.0, at):.3f}", "-i", str(src), "-frames:v", "1", "-vf", f"scale={width}:-2",
         "-q:v", "3", str(dst)], cleanup=[dst])
    return dst
