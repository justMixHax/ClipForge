"""Correct word timings with the audio itself.

Speech models place words well inside a phrase but tend to stretch the first word after a pause back
into the silence (a one-syllable word "lasting" a second), and now and then drop a word into a gap. For
captions that means a word lighting up before it is spoken. Here each word is checked against a fine
loudness envelope (100 values per second) and trimmed or moved to where there is actually sound.

When the recording has no quiet parts at all (constant game audio under the voice) there is nothing to
measure against, and the model's timings are kept unchanged.
"""
from __future__ import annotations

import math
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .sentences import Word

ENV_HZ = 100
MIN_TRIM = 0.12      # leading or trailing silence shorter than this is left alone
EDGE = 0.03          # keep this much air around a trimmed word
MIN_WORD = 0.06


@dataclass
class Envelope:
    level_db: np.ndarray
    hz: int
    threshold_db: float
    usable: bool          # False when the audio has no silence to compare against

    @property
    def duration(self) -> float:
        return self.level_db.size / self.hz

    def frame(self, t: float) -> int:
        return min(self.level_db.size, max(0, int(round(t * self.hz))))

    def voiced(self, start: float, end: float) -> float:
        """Share of the time range that contains sound above the silence threshold."""
        a, b = self.frame(start), self.frame(end)
        if b <= a:
            return 0.0
        return float(np.mean(self.level_db[a:b] > self.threshold_db))


def speech_envelope(wav_path: Path) -> Envelope:
    levels: list[np.ndarray] = []
    with wave.open(str(wav_path), "rb") as wf:
        if wf.getsampwidth() != 2 or wf.getnchannels() != 1:
            raise ValueError("expected 16-bit mono WAV")
        frame = max(1, wf.getframerate() // ENV_HZ)
        while True:
            raw = wf.readframes(frame * ENV_HZ * 60)
            if not raw:
                break
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            n = samples.size // frame
            if n == 0:
                break
            rms = np.sqrt(np.mean(samples[: n * frame].reshape(n, frame) ** 2, axis=1))
            levels.append(20.0 * np.log10(np.maximum(rms, 1e-5)))
    level_db = np.concatenate(levels).astype(np.float32) if levels else np.zeros(0, np.float32)
    if level_db.size == 0:
        return Envelope(level_db, ENV_HZ, -100.0, usable=False)
    p5, p95 = (float(v) for v in np.percentile(level_db, [5, 95]))
    spread = p95 - p5
    if spread < 12.0:
        return Envelope(level_db, ENV_HZ, p5 - 6.0, usable=False)
    return Envelope(level_db, ENV_HZ, min(p5 + 0.3 * spread, p95 - 10.0), usable=True)


def refine_word_times(words: list[Word], envelope: Envelope) -> list[Word]:
    if not envelope.usable or not words:
        return words
    level, hz, threshold = envelope.level_db, envelope.hz, envelope.threshold_db
    out: list[Word] = []
    previous_end = 0.0
    for k, word in enumerate(words):
        next_start = words[k + 1].start if k + 1 < len(words) else envelope.duration
        start, end = word.start, word.end
        a, b = envelope.frame(start), envelope.frame(end)
        mask = level[a:b] > threshold

        if end - start < MIN_WORD:
            # no usable length from the model: give it a short slot before the next word
            end = min(max(next_start, start + MIN_WORD), start + 0.2)
            if end - start < MIN_WORD:
                start = max(previous_end, end - 0.12)
        elif mask.size and not mask.any():
            # timed entirely in silence: it belongs with the next word if there is sound right before that
            f_next = envelope.frame(next_start)
            limit = max(envelope.frame(previous_end), f_next - int(0.6 * hz))
            v0 = f_next
            while v0 > limit and level[v0 - 1] > threshold:
                v0 -= 1
            if f_next - v0 >= int(MIN_WORD * hz) and next_start - end <= 4.0:
                start, end = v0 / hz, next_start - 0.01
        elif mask.size:
            voiced = np.flatnonzero(mask)
            lead = voiced[0] / hz
            trail = (mask.size - 1 - voiced[-1]) / hz
            if lead >= MIN_TRIM:
                start = start + lead - EDGE
            if trail >= MIN_TRIM:
                end = end - trail + EDGE

        start = max(start, previous_end)
        end = min(end, max(next_start, start + MIN_WORD))
        if end - start < MIN_WORD:
            end = start + MIN_WORD
        start, end = round(start, 3), round(end, 3)
        out.append(Word(word.text, start, end, word.prob))
        previous_end = end
    return out


def natural_duration(text: str) -> float:
    """A generous upper bound for how long a word takes to say."""
    return 0.25 + 0.085 * sum(ch.isalnum() for ch in text)


def timing_report(before: list[Word], after: list[Word]) -> dict[str, float]:
    """How much the correction changed, for the job log."""
    shifts = [abs(a.start - b.start) for a, b in zip(before, after)]
    moved = [s for s in shifts if s >= 0.1]
    return {"words": len(before), "moved": len(moved), "max_shift": round(max(shifts, default=0.0), 2),
            "mean_shift_of_moved": round(sum(moved) / len(moved), 2) if moved else 0.0,
            "share_moved": round(len(moved) / max(1, len(before)), 3) if before else 0.0,
            "finite": float(all(math.isfinite(w.start) and math.isfinite(w.end) for w in after))}
