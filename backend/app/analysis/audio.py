"""Audio signals for highlight discovery, computed from the 16 kHz mono WAV at 10 values per second.

The key idea (taken from the GTA 6 Clipper reference) is that loudness is judged against the *local*
baseline, a rolling median of the surrounding two minutes, never against an absolute level. A streamer
who is always loud does not light up the whole timeline; only moments that stand out from their
surroundings do.
"""
from __future__ import annotations

import json
import math
import os
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..errors import JobCancelled

HOP = 0.1                  # seconds per analysis frame
BASELINE_HALF_WINDOW = 60  # seconds either side used for the rolling baseline
LOUD_DB = 8.0              # this far above the baseline counts as "got loud"
FLOOR_DB = -100.0


def _runs(mask: np.ndarray, min_len: int, merge_gap: int = 0) -> list[tuple[int, int]]:
    """Index ranges [start, end) where mask is true, merging runs separated by short gaps."""
    if mask.size == 0 or not mask.any():
        return []
    padded = np.concatenate([[False], mask, [False]])
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    runs = [(int(a), int(b)) for a, b in zip(edges[::2], edges[1::2])]
    merged: list[tuple[int, int]] = []
    for a, b in runs:
        if merged and a - merged[-1][1] <= merge_gap:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return [(a, b) for a, b in merged if b - a >= min_len]


def _smooth(values: np.ndarray, width: int = 5) -> np.ndarray:
    if values.size < width:
        return values.astype(np.float32, copy=True)
    return np.convolve(values, np.ones(width, dtype=np.float32) / width, mode="same")


@dataclass
class AudioFeatures:
    hop: float
    duration: float
    rms_db: np.ndarray           # level of each frame
    delta_db: np.ndarray         # level relative to the rolling local baseline
    flux: np.ndarray             # sharp-onset strength in the 1-6 kHz band (impacts, bangs, claps)
    silence_db: float            # frames below this level count as silence
    speech_level_db: float       # typical level of the non-silent audio
    flux_ref: float = 1.0        # 95th percentile of flux, for normalising
    silences: list[tuple[float, float]] = field(default_factory=list)
    loud_events: list[dict] = field(default_factory=list)

    # -- queries --------------------------------------------------------------------------------------
    def _range(self, start: float, end: float) -> tuple[int, int]:
        n = self.rms_db.size
        return max(0, int(start / self.hop)), min(n, int(math.ceil(end / self.hop)))

    def stats(self, start: float, end: float) -> dict[str, float]:
        """Summary of one time window, used by the scorer and the quality checker."""
        empty = {"mean_delta": 0.0, "peak_delta": 0.0, "peak_time": float(start), "silence_ratio": 0.0,
                 "dyn_range": 0.0, "flux_peak": 0.0, "level_db": FLOOR_DB}
        i0, i1 = self._range(start, end)
        if i1 <= i0:
            return empty
        level, delta = self.rms_db[i0:i1], self.delta_db[i0:i1]
        active = level > self.silence_db
        if not active.any():
            return {**empty, "silence_ratio": 1.0}
        smooth = np.where(active, _smooth(delta), -np.inf)
        peak = int(np.argmax(smooth))
        p20, p95 = np.percentile(level[active], [20, 95])
        return {
            "mean_delta": float(delta[active].mean()),
            "peak_delta": float(max(0.0, smooth[peak])),
            "peak_time": float((i0 + peak) * self.hop + self.hop / 2),
            "silence_ratio": float(1.0 - active.mean()),
            "dyn_range": float(p95 - p20),
            "flux_peak": float(self.flux[i0:i1].max() / max(self.flux_ref, 1e-6)),
            "level_db": float(np.median(level[active])),
        }

    def quietest_time(self, start: float, end: float) -> float:
        """The quietest instant in a window: the safest place to cut."""
        i0, i1 = self._range(start, end)
        if i1 <= i0:
            return (start + end) / 2
        return float((i0 + int(np.argmin(self.rms_db[i0:i1]))) * self.hop + self.hop / 2)

    def envelope(self, points: int = 600) -> list[float]:
        """Loudness curve scaled 0..1 for drawing a timeline."""
        if self.rms_db.size == 0:
            return []
        lo = min(self.silence_db, float(np.percentile(self.rms_db, 5)))
        hi = float(self.rms_db.max())
        scaled = np.clip((self.rms_db - lo) / max(hi - lo, 1e-6), 0, 1)
        if scaled.size <= points:
            return [round(float(v), 3) for v in scaled]
        edges = np.linspace(0, scaled.size, points + 1).astype(int)
        return [round(float(scaled[a:max(b, a + 1)].max()), 3) for a, b in zip(edges[:-1], edges[1:])]

    # -- persistence ----------------------------------------------------------------------------------
    def save(self, path: Path) -> None:
        meta = {"hop": self.hop, "duration": self.duration, "silence_db": self.silence_db,
                "speech_level_db": self.speech_level_db, "flux_ref": self.flux_ref,
                "silences": self.silences, "loud_events": self.loud_events}
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "wb") as fh:
            np.savez_compressed(fh, rms_db=self.rms_db, delta_db=self.delta_db, flux=self.flux,
                                meta=np.array(json.dumps(meta)))
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: Path) -> AudioFeatures:
        with np.load(path, allow_pickle=False) as data:
            meta = json.loads(str(data["meta"]))
            return cls(hop=meta["hop"], duration=meta["duration"], rms_db=data["rms_db"],
                       delta_db=data["delta_db"], flux=data["flux"], silence_db=meta["silence_db"],
                       speech_level_db=meta["speech_level_db"], flux_ref=meta["flux_ref"],
                       silences=[(float(a), float(b)) for a, b in meta["silences"]],
                       loud_events=meta["loud_events"])


def _read_levels(wav_path: Path, should_cancel: Callable[[], bool] | None,
                 on_progress: Callable[[float], None] | None) -> tuple[np.ndarray, np.ndarray, float]:
    levels: list[np.ndarray] = []
    fluxes: list[np.ndarray] = []
    with wave.open(str(wav_path), "rb") as wf:
        if wf.getsampwidth() != 2 or wf.getnchannels() != 1:
            raise ValueError("audio analysis expects 16-bit mono WAV")
        rate = wf.getframerate()
        total = wf.getnframes()
        frame = max(1, int(rate * HOP))
        window = np.hanning(frame).astype(np.float32)
        lo = int(1000 * frame / rate)
        hi = max(lo + 1, min(int(6000 * frame / rate), frame // 2))
        prev_band: np.ndarray | None = None
        done = 0
        while True:
            raw = wf.readframes(frame * 600)  # one minute at a time keeps memory flat for long streams
            if not raw:
                break
            if should_cancel is not None and should_cancel():
                raise JobCancelled()
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            n = samples.size // frame
            done += samples.size
            if n == 0:
                break
            block = samples[: n * frame].reshape(n, frame)
            rms = np.sqrt(np.mean(block * block, axis=1))
            levels.append(20.0 * np.log10(np.maximum(rms, 10 ** (FLOOR_DB / 20))))
            band = np.abs(np.fft.rfft(block * window, axis=1))[:, lo:hi]
            before = np.vstack([prev_band if prev_band is not None else band[:1], band[:-1]])
            fluxes.append(np.maximum(band - before, 0).sum(axis=1) / (before.sum(axis=1) + 1e-6))
            prev_band = band[-1:]
            if on_progress is not None and total:
                on_progress(min(0.99, done / total))
    if not levels:
        return np.zeros(0, np.float32), np.zeros(0, np.float32), 0.0
    rms_db = np.concatenate(levels).astype(np.float32)
    return rms_db, np.concatenate(fluxes).astype(np.float32), rms_db.size * HOP


def _baseline(rms_db: np.ndarray, active: np.ndarray, fallback: float) -> np.ndarray:
    """Rolling median of the non-silent level, one value per second, stretched back to frame rate."""
    per_sec = int(round(1 / HOP))
    n = rms_db.size
    n_sec = int(math.ceil(n / per_sec))
    grid = np.full(n_sec * per_sec, np.nan, dtype=np.float32)
    grid[:n] = np.where(active, rms_db, np.nan)
    grid = grid.reshape(n_sec, per_sec)
    counts = np.sum(~np.isnan(grid), axis=1)
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            seconds = np.nanmedian(grid, axis=1)
    seconds[counts < 3] = np.nan

    half = min(BASELINE_HALF_WINDOW, n_sec - 1)
    if half >= 1:
        padded = np.pad(seconds, half, mode="reflect")
        windows = np.lib.stride_tricks.sliding_window_view(padded, 2 * half + 1)
        with np.errstate(all="ignore"):
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=RuntimeWarning)
                rolled = np.nanmedian(windows, axis=1)
    else:
        rolled = seconds.copy()
    rolled = np.where(np.isnan(rolled), fallback, rolled)
    centres = (np.arange(n_sec) + 0.5) * per_sec
    return np.interp(np.arange(n) + 0.5, centres, rolled).astype(np.float32)


def compute_audio_features(wav_path: Path, *, should_cancel: Callable[[], bool] | None = None,
                           on_progress: Callable[[float], None] | None = None) -> AudioFeatures:
    rms_db, flux, duration = _read_levels(wav_path, should_cancel, on_progress)
    if rms_db.size == 0:
        return AudioFeatures(hop=HOP, duration=0.0, rms_db=rms_db, delta_db=rms_db.copy(), flux=flux,
                             silence_db=FLOOR_DB, speech_level_db=FLOOR_DB)

    p5, p95 = (float(v) for v in np.percentile(rms_db, [5, 95]))
    spread = p95 - p5
    if spread < 12.0:
        # No real quiet parts: nothing counts as silence, everything counts as active audio.
        silence_db = p5 - 6.0
    else:
        silence_db = min(p5 + 0.25 * spread, p95 - 12.0)
    active = rms_db > silence_db
    speech_level = float(np.median(rms_db[active])) if active.any() else float(np.median(rms_db))

    baseline = _baseline(rms_db, active, speech_level)
    delta = np.clip(rms_db - baseline, -40.0, 40.0).astype(np.float32)

    loud_mask = (_smooth(delta) >= LOUD_DB) & active
    loud_events = []
    for a, b in _runs(loud_mask, min_len=3, merge_gap=int(1.0 / HOP)):
        loud_events.append({"start": round(a * HOP, 2), "end": round(b * HOP, 2),
                            "peak_db": round(float(_smooth(delta)[a:b].max()), 1)})

    silences = [(round(a * HOP, 2), round(b * HOP, 2)) for a, b in _runs(~active, min_len=int(0.5 / HOP))]

    if on_progress is not None:
        on_progress(1.0)
    return AudioFeatures(hop=HOP, duration=duration, rms_db=rms_db, delta_db=delta, flux=flux,
                         silence_db=float(silence_db), speech_level_db=speech_level,
                         flux_ref=float(max(np.percentile(flux, 95), 1e-6)),
                         silences=silences, loud_events=loud_events)
