"""Builders for synthetic transcripts and signals, so scoring tests need no media files."""
from __future__ import annotations

import numpy as np

from app.analysis.audio import HOP, AudioFeatures
from app.analysis.visual import VisualFeatures
from app.transcription.sentences import Sentence, Word, build_sentences

Line = tuple[str, float]  # (spoken text, pause after it in seconds)


def script_to_words(lines: list[Line], start: float = 0.0, wps: float = 3.0, prob: float = 0.95) -> list[Word]:
    """Lay the lines out in time at a steady speaking rate."""
    words: list[Word] = []
    t = start
    step = 1.0 / wps
    for text, pause in lines:
        for token in text.split():
            words.append(Word(token, round(t, 3), round(t + step * 0.85, 3), prob))
            t += step
        t += pause
    return words


def transcript(lines: list[Line], **kw) -> tuple[list[Word], list[Sentence]]:
    words = script_to_words(lines, **kw)
    return words, build_sentences(words)


def find(sentences: list[Sentence], starts_with: str) -> int:
    """Index of the first sentence whose text starts with the given words."""
    for s in sentences:
        if s.text.lower().startswith(starts_with.lower()):
            return s.idx
    raise AssertionError(f"no sentence starts with {starts_with!r}: {[s.text for s in sentences]}")


def make_audio(duration: float, loud: list[tuple[float, float, float]] = (),
               silent: list[tuple[float, float]] = ()) -> AudioFeatures:
    """Speech at a steady level; `loud` ranges are (start, end, dB above baseline)."""
    n = int(duration / HOP)
    delta = np.zeros(n, dtype=np.float32)
    for a, b, db in loud:
        delta[int(a / HOP):int(b / HOP)] = db
    rms = (-25.0 + delta).astype(np.float32)
    for a, b in silent:
        rms[int(a / HOP):int(b / HOP)] = -70.0
        delta[int(a / HOP):int(b / HOP)] = -40.0
    events = [{"start": a, "end": b, "peak_db": db} for a, b, db in loud if db >= 8]
    return AudioFeatures(hop=HOP, duration=duration, rms_db=rms, delta_db=delta,
                         flux=np.zeros(n, dtype=np.float32), silence_db=-50.0, speech_level_db=-25.0,
                         silences=[(float(a), float(b)) for a, b in silent], loud_events=events)


def make_visual(duration: float, busy: list[tuple[float, float, float]] = (), base: float = 0.01,
                fps: float = 2.0) -> VisualFeatures:
    """A calm picture; `busy` ranges are (start, end, motion level 0..1)."""
    n = int(duration * fps)
    motion = np.full(n, base, dtype=np.float32)
    for a, b, level in busy:
        motion[int(a * fps):int(b * fps)] = level
    return VisualFeatures(fps=fps, duration=duration, motion=motion,
                          brightness=np.full(n, 0.5, dtype=np.float32), cuts=[],
                          motion_ref=max(float(np.percentile(motion, 90)), 0.02))


class ScriptedModel:
    """Stands in for the speech model in pipeline tests: 'hears' a fixed script whatever the audio is."""

    def __init__(self, lines: list[Line], on_call=None) -> None:
        self.words = script_to_words(lines)
        self.calls = 0
        self.on_call = on_call

    def __call__(self, audio, language, on_fraction=None, should_cancel=None):  # noqa: ANN001
        from app.transcription.whisper_engine import SAMPLE_RATE, WindowResult

        self.calls += 1
        if self.on_call is not None:
            self.on_call()
        if should_cancel is not None and should_cancel():
            from app.errors import JobCancelled
            raise JobCancelled()
        limit = audio.size / SAMPLE_RATE
        if on_fraction:
            on_fraction(1.0)
        return WindowResult(words=[w for w in self.words if w.end <= limit], language="en", language_probability=1.0)
