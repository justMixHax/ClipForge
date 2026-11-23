"""Visual signals: how much the picture moves, where it cuts, and whether it is dark.

One FFmpeg process decodes the video into tiny grey frames (160 px wide, 2 per second) and numpy does
the rest, so a long stream is analysed without ever holding full frames in memory. These signals let the
scorer see action that happens in the gameplay even when nobody is talking.
"""
from __future__ import annotations

import itertools
import json
import math
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..services import ffmpeg

SAMPLE_FPS = 2.0
ANALYSIS_WIDTH = 160
STATIC_MOTION = 0.004   # below this the picture is effectively frozen
DARK_LEVEL = 0.08       # below this the frame is effectively black
CUT_HISTOGRAM = 0.40    # share of pixels that changed brightness band
CUT_MOTION = 0.06


@dataclass
class VisualFeatures:
    fps: float
    duration: float
    motion: np.ndarray       # mean absolute change from the previous sample, 0..1
    brightness: np.ndarray   # mean brightness, 0..1
    cuts: list[float] = field(default_factory=list)  # times of hard scene changes
    motion_ref: float = 0.02  # 90th percentile of motion across the whole video

    def _range(self, start: float, end: float) -> tuple[int, int]:
        n = self.motion.size
        return max(0, int(math.ceil(start * self.fps - 1e-9))), min(n, int(math.floor(end * self.fps + 1e-9)) + 1)

    def stats(self, start: float, end: float) -> dict[str, float]:
        i0, i1 = self._range(start, end)
        cut_count = sum(1 for t in self.cuts if start <= t < end)
        if i1 <= i0:
            return {"motion_mean": 0.0, "activity": 0.0, "cut_count": cut_count, "dark_ratio": 0.0,
                    "static_ratio": 0.0, "samples": 0}
        motion, bright = self.motion[i0:i1], self.brightness[i0:i1]
        mean = float(motion.mean())
        return {
            "motion_mean": mean,
            # activity compares this window with the busiest tenth of the same video
            "activity": float(min(1.0, mean / max(self.motion_ref, 0.02))),
            "cut_count": cut_count,
            "dark_ratio": float((bright < DARK_LEVEL).mean()),
            "static_ratio": float((motion < STATIC_MOTION).mean()),
            "samples": int(i1 - i0),
        }

    def peak_time(self, start: float, end: float) -> float:
        i0, i1 = self._range(start, end)
        if i1 <= i0:
            return (start + end) / 2
        return float((i0 + int(np.argmax(self.motion[i0:i1]))) / self.fps)

    def save(self, path: Path) -> None:
        meta = {"fps": self.fps, "duration": self.duration, "cuts": self.cuts, "motion_ref": self.motion_ref}
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "wb") as fh:
            np.savez_compressed(fh, motion=self.motion, brightness=self.brightness, meta=np.array(json.dumps(meta)))
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: Path) -> VisualFeatures:
        with np.load(path, allow_pickle=False) as data:
            meta = json.loads(str(data["meta"]))
            return cls(fps=meta["fps"], duration=meta["duration"], motion=data["motion"],
                       brightness=data["brightness"], cuts=[float(t) for t in meta["cuts"]],
                       motion_ref=meta["motion_ref"])


def analysis_size(src_width: int, src_height: int, width: int = ANALYSIS_WIDTH) -> tuple[int, int]:
    height = max(16, int(round(width * src_height / max(1, src_width) / 2)) * 2)
    return width, height


def compute_visual_features(video_path: Path, duration: float, *, src_size: tuple[int, int] | None = None,
                            sample_fps: float = SAMPLE_FPS, fast_decode: bool = True,
                            on_progress: Callable[[float], None] | None = None,
                            should_cancel: Callable[[], bool] | None = None) -> VisualFeatures:
    """fast_decode (the default) skips frames nothing depends on: about 40% faster on long streams, the same
    signals within a fraction of a percent. A file whose decoder refuses that is read again in full."""
    if src_size is None:
        info = ffmpeg.probe(video_path)
        src_size = (info.width, info.height)
    width, height = analysis_size(*src_size)
    expected = max(1, int(duration * sample_fps))

    motion: list[float] = []
    brightness: list[float] = []
    hist_change: list[float] = []
    prev: np.ndarray | None = None
    prev_hist: np.ndarray | None = None
    frames = ffmpeg.iter_frames(video_path, fps=sample_fps, width=width, height=height, pix_fmt="gray",
                                fast_decode=fast_decode, should_cancel=should_cancel)
    if fast_decode:
        first = next(frames, None)
        if first is None:  # nothing came out: read it the ordinary way
            frames = ffmpeg.iter_frames(video_path, fps=sample_fps, width=width, height=height, pix_fmt="gray",
                                        fast_decode=False, should_cancel=should_cancel)
        else:
            frames = itertools.chain([first], frames)
    for k, frame in enumerate(frames):
        signed = frame.astype(np.int16)
        hist = np.bincount((frame >> 4).ravel(), minlength=16) / frame.size
        if prev is None:
            motion.append(0.0)
            hist_change.append(0.0)
        else:
            motion.append(float(np.abs(signed - prev).mean()) / 255.0)
            hist_change.append(0.5 * float(np.abs(hist - prev_hist).sum()))
        brightness.append(float(frame.mean()) / 255.0)
        prev, prev_hist = signed, hist
        if on_progress is not None and k % 20 == 0:
            on_progress(min(0.99, (k + 1) / expected))

    cuts: list[float] = []
    for k in range(1, len(motion)):
        if hist_change[k] > CUT_HISTOGRAM and motion[k] > CUT_MOTION:
            t = k / sample_fps
            if not cuts or t - cuts[-1] >= 1.0:
                cuts.append(round(t, 2))

    motion_arr = np.asarray(motion, dtype=np.float32)
    if cuts and motion_arr.size:
        # the jump at a cut is an edit, not movement: do not let it inflate the activity scale
        steady = motion_arr.copy()
        for t in cuts:
            steady[int(round(t * sample_fps))] = 0.0
        ref = float(np.percentile(steady, 90))
    else:
        ref = float(np.percentile(motion_arr, 90)) if motion_arr.size else 0.0
    if on_progress is not None:
        on_progress(1.0)
    return VisualFeatures(fps=sample_fps, duration=duration, motion=motion_arr,
                          brightness=np.asarray(brightness, dtype=np.float32), cuts=cuts,
                          motion_ref=max(ref, 0.02))
