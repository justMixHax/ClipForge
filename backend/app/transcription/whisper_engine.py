"""Speech to text with word-level timestamps (faster-whisper), built for long recordings.

The audio is read in windows of a few minutes, cut at the quietest spot near each window's end so no
word is split. After every window a checkpoint is written, so a job that is interrupted (crash, cancel,
power cut) continues from the last finished window instead of starting again. Memory use stays flat no
matter how long the stream is.

The windowing logic takes the model as a plain function, so it is tested without loading a model.
"""
from __future__ import annotations

import json
import os
import re
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from ..errors import JobCancelled
from .sentences import Word

SAMPLE_RATE = 16000
_NOISE = re.compile(r"^(\[[^\]]*\]|\([^)]*\)|\*[^*]*\*|[♪♫\s]+)$")


@dataclass
class TranscribeOptions:
    language: str | None = None      # None = detect from the first window
    window_seconds: float = 300.0
    search_seconds: float = 15.0     # how far back from a window's end to look for a quiet cut point


@dataclass
class WindowResult:
    words: list[Word]                # times relative to the start of the window
    language: str
    language_probability: float


@dataclass
class TranscriptResult:
    words: list[Word]
    language: str
    language_probability: float
    duration: float
    windows: int
    resumed_windows: int = 0


class WindowTranscriber(Protocol):
    def __call__(self, audio: np.ndarray, language: str | None,
                 on_fraction: Callable[[float], None] | None = None,
                 should_cancel: Callable[[], bool] | None = None) -> WindowResult: ...


def is_noise_token(text: str) -> bool:
    """Non-speech markers such as [Music] or (applause): kept out of the transcript and captions."""
    return bool(_NOISE.match(text.strip()))


def collapse_repeated_segments(texts: list[str]) -> list[int]:
    """Indexes of segments to keep. The model sometimes loops on silence or music ("Thanks for watching."
    over and over); from the third identical multi-word segment in a row, the repeats are dropped."""
    keep: list[int] = []
    streak = 0
    previous = None
    for index, text in enumerate(texts):
        key = text.strip().lower()
        streak = streak + 1 if key == previous else 1
        previous = key
        if streak >= 3 and len(key.split()) >= 2:
            continue
        keep.append(index)
    return keep


def merge_fragments(words: list[Word]) -> list[Word]:
    """Join pieces the model emits separately onto the word before them: the second half of a hyphenated
    word ("Open" + "-world") and stray punctuation ("games" + ",")."""
    merged: list[Word] = []
    for word in words:
        text = word.text.strip()
        is_fragment = (text[:1] in "-–" and len(text) > 1) or (text != "" and all(not ch.isalnum() for ch in text))
        if merged and is_fragment:
            head = merged[-1]
            merged[-1] = Word(head.text + text, head.start, max(head.end, word.end), min(head.prob, word.prob))
        else:
            merged.append(Word(text, word.start, word.end, word.prob))
    return merged


def split_point(samples: np.ndarray, search_seconds: float = 15.0, frame_seconds: float = 0.3) -> int:
    """Where to end this window: the middle of the quietest stretch within the last search_seconds."""
    n = samples.size
    frame = int(frame_seconds * SAMPLE_RATE)
    lo = max(0, n - int(search_seconds * SAMPLE_RATE))
    count = (n - lo) // frame
    if count < 2:
        return n
    region = samples[lo:lo + count * frame].reshape(count, frame)
    quietest = int(np.argmin(np.mean(region * region, axis=1)))
    return lo + quietest * frame + frame // 2


def _write_checkpoint(path: Path, payload: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)


def transcribe_wav(wav_path: Path, window_fn: WindowTranscriber, options: TranscribeOptions, *,
                   checkpoint_path: Path | None = None, signature: str = "",
                   on_progress: Callable[[float], None] | None = None,
                   should_cancel: Callable[[], bool] | None = None) -> TranscriptResult:
    """Transcribe a 16 kHz mono 16-bit WAV. `signature` identifies the model and settings: a checkpoint is
    only reused when it was written with the same signature for the same audio."""
    with wave.open(str(wav_path), "rb") as wf:
        if wf.getframerate() != SAMPLE_RATE or wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise ValueError("transcription expects 16 kHz mono 16-bit WAV")
        total = wf.getnframes()
        window = max(SAMPLE_RATE, int(options.window_seconds * SAMPLE_RATE))

        pos = 0
        words: list[Word] = []
        language = options.language
        probability = 1.0 if language else 0.0
        windows = resumed = 0

        if checkpoint_path is not None and checkpoint_path.exists():
            try:
                saved = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                saved = {}
            if (saved.get("signature") == signature and saved.get("total") == total
                    and 0 < int(saved.get("next_sample", 0)) <= total):
                pos = int(saved["next_sample"])
                words = [Word(t, s, e, p) for t, s, e, p in saved.get("words", [])]
                language = options.language or saved.get("language") or None
                probability = float(saved.get("language_probability", 0.0))
                windows = resumed = int(saved.get("windows", 0))

        while pos < total:
            if should_cancel is not None and should_cancel():
                raise JobCancelled()
            remaining = total - pos
            # avoid leaving a sliver of under two seconds for a window of its own
            take = remaining if remaining - window < 2 * SAMPLE_RATE else window
            wf.setpos(pos)
            samples = np.frombuffer(wf.readframes(take), dtype=np.int16).astype(np.float32) / 32768.0
            if samples.size == 0:
                break
            if take < remaining:
                samples = samples[:split_point(samples, options.search_seconds)]
            offset = pos / SAMPLE_RATE
            span = samples.size

            def report(fraction: float, _pos: int = pos, _span: int = span) -> None:
                if on_progress is not None:
                    on_progress(min(1.0, (_pos + max(0.0, min(1.0, fraction)) * _span) / total))

            if samples.size >= SAMPLE_RATE // 10:
                result = window_fn(samples, language, report, should_cancel)
                if language is None:
                    language, probability = result.language, result.language_probability
                limit = total / SAMPLE_RATE
                for w in result.words:
                    words.append(Word(w.text, round(w.start + offset, 3), round(min(w.end + offset, limit), 3),
                                      round(float(w.prob), 3)))
            pos += span
            windows += 1
            report(1.0, pos - span, span)
            if checkpoint_path is not None and pos < total:
                _write_checkpoint(checkpoint_path, {
                    "signature": signature, "total": total, "next_sample": pos, "windows": windows,
                    "language": language, "language_probability": probability,
                    "words": [[w.text, w.start, w.end, w.prob] for w in words]})

    if checkpoint_path is not None:
        checkpoint_path.unlink(missing_ok=True)
    if on_progress is not None:
        on_progress(1.0)
    return TranscriptResult(words=words, language=language or "", language_probability=probability,
                            duration=total / SAMPLE_RATE, windows=windows, resumed_windows=resumed)


class FasterWhisperTranscriber:
    """The real model. Loading takes a second or two, so instances are cached by get_transcriber()."""

    def __init__(self, model_path: Path | str, device: str = "cpu", compute_type: str = "int8",
                 cpu_threads: int = 0, beam_size: int = 5) -> None:
        from faster_whisper import WhisperModel

        threads = cpu_threads or max(1, min(8, (os.cpu_count() or 4) // 2))
        self.model = WhisperModel(str(model_path), device=device, compute_type=compute_type, cpu_threads=threads)
        self.multilingual = bool(self.model.model.is_multilingual)
        self.beam_size = beam_size
        self.device, self.compute_type = device, compute_type

    def __call__(self, audio: np.ndarray, language: str | None,
                 on_fraction: Callable[[float], None] | None = None,
                 should_cancel: Callable[[], bool] | None = None) -> WindowResult:
        if not self.multilingual:
            language = "en"  # the ".en" models only know English
        segments, info = self.model.transcribe(
            audio, language=language, beam_size=self.beam_size, word_timestamps=True, vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500}, condition_on_previous_text=False)
        seconds = max(audio.size / SAMPLE_RATE, 0.1)
        kept: list[tuple[str, list[Word]]] = []
        for segment in segments:  # a generator: the model works as we iterate
            if should_cancel is not None and should_cancel():
                raise JobCancelled()
            if on_fraction is not None:
                on_fraction(min(1.0, segment.end / seconds))
            if segment.no_speech_prob > 0.6 and segment.avg_logprob < -1.0:
                continue  # almost certainly not speech
            heard = merge_fragments([Word(w.word.strip(), float(w.start), float(w.end), float(w.probability))
                                     for w in (segment.words or []) if w.word.strip() and not is_noise_token(w.word)])
            if heard:
                kept.append((segment.text, heard))
        keep = collapse_repeated_segments([text for text, _ in kept])
        return WindowResult(words=[w for k in keep for w in kept[k][1]], language=info.language or (language or ""),
                            language_probability=float(info.language_probability or 0.0))


_CACHE: dict[tuple[str, str, str], FasterWhisperTranscriber] = {}


def get_transcriber(model_path: Path | str, device: str, compute_type: str) -> FasterWhisperTranscriber:
    key = (str(model_path), device, compute_type)
    if key not in _CACHE:
        _CACHE.clear()  # one model in memory at a time
        _CACHE[key] = FasterWhisperTranscriber(model_path, device, compute_type)
    return _CACHE[key]
