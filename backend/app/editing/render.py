"""Render one clip with FFmpeg: cut, reframe, burn captions, normalise audio, encode.

Cutting is done inside the filter graph (trim/atrim on one timeline) after a coarse input seek, so the
picture and the sound are cut at exactly the same instant and the first frame is the requested frame.
FFmpeg runs inside the clip's work folder and is given only relative names for the subtitle file and
the font folder, which avoids Windows drive-letter escaping problems entirely.
"""
from __future__ import annotations

import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import ClipForgeError
from ..services import ffmpeg
from ..services.hardware import encoder_args
from .fonts import pick_font
from .follow import clip_points
from .reframe import ReframePlan, build_filter

SEEK_MARGIN = 2.0  # seconds decoded before the first segment so the cut itself is frame-accurate


@dataclass
class RenderRequest:
    source: Path
    out_path: Path
    work_dir: Path
    segments: list[tuple[float, float]]      # source time ranges that make up the clip, in order
    src_w: int
    src_h: int
    has_audio: bool
    plan: ReframePlan
    ass_text: str | None = None
    fps: int = 30
    encoder: str = "libx264"
    quality: str = "balanced"
    normalize_audio: bool = True
    denoise: bool = False
    audio_channels: int | None = None                 # None = read it from the source file when rendering
    fonts: list[Path] = field(default_factory=list)   # empty = work them out from the subtitle styles


@dataclass
class RenderResult:
    path: Path
    width: int
    height: int
    duration: float
    size_bytes: int
    encoder: str
    seconds_taken: float
    filter_graph: str


def _validate(segments: list[tuple[float, float]]) -> float:
    if not segments:
        raise ValueError("a clip needs at least one segment")
    previous_end = -1.0
    for start, end in segments:
        if end - start < 0.05:
            raise ValueError(f"segment {start:.2f}-{end:.2f} is empty or reversed")
        if start < previous_end - 1e-6:
            raise ValueError("segments must be in order and must not overlap")
        previous_end = end
    return sum(end - start for start, end in segments)


def _fonts_for(ass_text: str) -> list[Path]:
    """The font files behind the styles of a subtitle script."""
    paths: list[Path] = []
    for name in dict.fromkeys(re.findall(r"^Style:\s*[^,]+,([^,]+),", ass_text, flags=re.M)):
        font, _ = pick_font(name.strip())
        if font is not None and font.path not in paths:
            paths.append(font.path)
    return paths


def build_graph(req: RenderRequest) -> tuple[str, float, float]:
    """(filter graph, input seek position, clip length)."""
    total = _validate(req.segments)
    seek = max(0.0, req.segments[0][0] - SEEK_MARGIN)
    n = len(req.segments)
    parts: list[str] = []

    if n == 1:
        start, end = req.segments[0]
        parts.append(f"[0:v]trim=start={start - seek:.3f}:duration={end - start:.3f},setpts=PTS-STARTPTS,"
                     f"fps={req.fps}[vin]")
    else:
        parts.append(f"[0:v]split={n}" + "".join(f"[vs{k}]" for k in range(n)))
        for k, (start, end) in enumerate(req.segments):
            parts.append(f"[vs{k}]trim=start={start - seek:.3f}:duration={end - start:.3f},setpts=PTS-STARTPTS[vt{k}]")
        parts.append("".join(f"[vt{k}]" for k in range(n)) + f"concat=n={n}:v=1:a=0,fps={req.fps}[vin]")

    # A camera that follows the person: its path, kept in source time, in the time of the clip as it is cut.
    points = clip_points(req.plan.track, req.segments) if req.plan.track else None
    if req.ass_text:
        parts.append(build_filter(req.plan, "[vin]", "[base]", points))
        parts.append("[base]subtitles=f=captions.ass:fontsdir=fonts[v]")
    else:
        parts.append(build_filter(req.plan, "[vin]", "[v]", points))

    if req.has_audio:
        # A mono recording is copied to both speakers at its original level. Left to itself FFmpeg lowers
        # each side by 3 dB when it widens mono to stereo, which made clips quieter than their source.
        widen = "pan=stereo|c0=c0|c1=c0," if req.audio_channels == 1 else ""
        polish = widen + ("afftdn=nf=-25," if req.denoise else "") + \
                 ("loudnorm=I=-14:TP=-1.5:LRA=11," if req.normalize_audio else "") + "aresample=48000"
        if n == 1:
            start, end = req.segments[0]
            parts.append(f"[0:a]atrim=start={start - seek:.3f}:duration={end - start:.3f},asetpts=PTS-STARTPTS,"
                         f"{polish}[a]")
        else:
            parts.append(f"[0:a]asplit={n}" + "".join(f"[as{k}]" for k in range(n)))
            for k, (start, end) in enumerate(req.segments):
                parts.append(f"[as{k}]atrim=start={start - seek:.3f}:duration={end - start:.3f},"
                             f"asetpts=PTS-STARTPTS[at{k}]")
            parts.append("".join(f"[at{k}]" for k in range(n)) + f"concat=n={n}:v=0:a=1,{polish}[a]")
    return ";".join(parts), seek, total


def render_clip(req: RenderRequest, *, on_progress: Callable[[float], None] | None = None,
                should_cancel: Callable[[], bool] | None = None) -> RenderResult:
    if req.has_audio and req.audio_channels is None:
        try:
            req.audio_channels = ffmpeg.probe(req.source).audio_channels
        except ClipForgeError:
            pass  # an unreadable source: the render below fails and reports exactly what is wrong
    graph, seek, total = build_graph(req)
    req.work_dir.mkdir(parents=True, exist_ok=True)
    req.out_path.parent.mkdir(parents=True, exist_ok=True)

    if req.ass_text:
        (req.work_dir / "captions.ass").write_text(req.ass_text, encoding="utf-8")
        fonts_dir = req.work_dir / "fonts"
        fonts_dir.mkdir(exist_ok=True)
        for font in (req.fonts or _fonts_for(req.ass_text)):
            target = fonts_dir / font.name
            if not target.exists():
                shutil.copyfile(font, target)

    args = ["-ss", f"{seek:.3f}", "-i", str(req.source)]
    if req.has_audio:
        audio_map = "[a]"
    else:  # platforms expect an audio track: give silent sources a silent one
        args += ["-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        audio_map = "1:a"
    args += ["-filter_complex", graph, "-map", "[v]", "-map", audio_map,
             *encoder_args(req.encoder, req.quality), "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
             "-t", f"{total:.3f}", "-map_metadata", "-1", "-movflags", "+faststart", str(req.out_path.resolve())]

    started = time.monotonic()
    ffmpeg.run(args, duration=total, on_progress=on_progress, should_cancel=should_cancel, cwd=req.work_dir,
               cleanup=[req.out_path])
    info = ffmpeg.probe(req.out_path)
    return RenderResult(path=req.out_path, width=info.width, height=info.height, duration=info.duration,
                        size_bytes=req.out_path.stat().st_size, encoder=req.encoder,
                        seconds_taken=round(time.monotonic() - started, 2), filter_graph=graph)
