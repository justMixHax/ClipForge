"""The real speech model on the sample video, checked against the script the sample was built from.

The words are checked as the app stores them: the model's output after the word-timing correction that
the analysis job applies (app/transcription/align.py). The raw model output is known to contain the odd
zero-length word and words stretched across silence, which is exactly what that correction is for.
Slow (loads the model and transcribes ~3 minutes of audio). Skipped when the model or the sample is missing.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

from app.services import ffmpeg
from app.transcription.align import refine_word_times, speech_envelope
from app.transcription.sentences import build_sentences
from app.transcription.whisper_engine import FasterWhisperTranscriber, TranscribeOptions, transcribe_wav
from tests.conftest import ROOT, SAMPLE_TRUTH

pytestmark = [pytest.mark.slow, pytest.mark.ffmpeg]
MODEL_DIR = ROOT / "data" / "models" / "whisper" / "small.en"


def norm(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


@pytest.fixture(scope="module")
def transcript(sample_video: Path, tmp_path_factory):  # noqa: ANN001
    if not (MODEL_DIR / "model.bin").exists():
        pytest.skip("speech model small.en is not installed in data/models/whisper")
    wav = ffmpeg.extract_audio(sample_video, tmp_path_factory.mktemp("audio") / "audio.wav")
    model = FasterWhisperTranscriber(MODEL_DIR, device="cpu", compute_type="int8")
    started = time.monotonic()
    result = transcribe_wav(wav, model, TranscribeOptions(window_seconds=60.0))
    result.raw_words = list(result.words)  # type: ignore[attr-defined]
    result.words = refine_word_times(result.words, speech_envelope(wav))  # what the analysis job stores
    result.seconds_taken = time.monotonic() - started  # type: ignore[attr-defined]
    return result


@pytest.fixture(scope="module")
def truth() -> dict:
    return json.loads(SAMPLE_TRUTH.read_text(encoding="utf-8"))


def test_the_key_phrases_of_every_planted_moment_are_heard(transcript):  # noqa: ANN001
    heard = " ".join(norm(" ".join(w.text for w in transcript.words)))
    for phrase in ["one health point", "headshot through the wall", "flashlight on my phone", "hot take",
                   "bigger is not better", "fell off the map", "three hours of progress"]:
        assert phrase in heard, f"not heard: {phrase}"


def test_most_of_the_script_is_transcribed_word_for_word(transcript, truth):  # noqa: ANN001
    spoken = norm(" ".join(line["text"] for line in truth["lines"]))
    heard = set(norm(" ".join(w.text for w in transcript.words)))
    recall = sum(1 for token in spoken if token in heard) / len(spoken)
    assert recall >= 0.9, f"only {recall:.0%} of the scripted words were heard"


def test_word_timestamps_match_when_the_lines_were_actually_spoken(transcript, truth):  # noqa: ANN001
    """Each scripted line's first distinctive word must be timed inside that line (within half a second)."""
    checked = 0
    for line in truth["lines"]:
        tokens = [t for t in norm(line["text"]) if len(t) >= 6]
        if not tokens:
            continue
        target = tokens[0]
        hits = [w for w in transcript.words if norm(w.text) == [target]
                and line["start"] - 0.5 <= w.start <= line["end"] + 0.5]
        assert hits, f"{target!r} not found between {line['start']:.1f}s and {line['end']:.1f}s"
        checked += 1
    assert checked >= 20


def test_words_are_in_order_with_sane_durations_and_confidence(transcript):  # noqa: ANN001
    words = transcript.words
    assert len(words) == len(transcript.raw_words)                 # the correction moves words, never drops them
    assert [w.text for w in words] == [w.text for w in transcript.raw_words]
    assert all(a.start <= b.start for a, b in zip(words, words[1:]))
    assert all(0 < w.end - w.start < 3.0 for w in words), [w for w in words if not 0 < w.end - w.start < 3.0]
    assert sum(w.prob for w in words) / len(words) > 0.8
    assert transcript.language == "en"


def test_sentences_follow_the_scripted_lines(transcript, truth):  # noqa: ANN001
    sentences = build_sentences(transcript.words)
    assert 40 <= len(sentences) <= 90
    assert any(s.text.lower().startswith("can i tell you") for s in sentences)


def test_transcription_is_faster_than_real_time_on_this_pc(transcript):  # noqa: ANN001
    assert transcript.seconds_taken < transcript.duration, (
        f"took {transcript.seconds_taken:.0f}s for {transcript.duration:.0f}s of audio")


# ------------------------------------------------------------------------------------ on the graphics card
KEY_PHRASES = ["one health point", "headshot through the wall", "flashlight on my phone", "hot take",
               "bigger is not better", "fell off the map", "three hours of progress"]


@pytest.fixture(scope="module")
def gpu_model():  # noqa: ANN201
    """The same speech model on the graphics card. Skipped unless it really loads and runs there
    (it needs an NVIDIA card and the runtime libraries from scripts/enable-gpu.ps1)."""
    from app.services import hardware

    if not (MODEL_DIR / "model.bin").exists():
        pytest.skip("speech model small.en is not installed in data/models/whisper")
    ok, detail = hardware.test_whisper_cuda(MODEL_DIR, hardware.gpu_info())
    if not ok:
        pytest.skip(f"speech-to-text on the graphics card is not available on this PC: {detail}")
    hardware.register_cuda_dlls()
    return FasterWhisperTranscriber(MODEL_DIR, device="cuda", compute_type="float16")


def test_the_graphics_card_hears_what_the_processor_hears_and_much_faster(transcript, gpu_model, sample_video: Path,
                                                                          tmp_path_factory):  # noqa: ANN001
    wav = ffmpeg.extract_audio(sample_video, tmp_path_factory.mktemp("gpu_audio") / "audio.wav")
    transcribe_wav(wav, gpu_model, TranscribeOptions(window_seconds=60.0))      # first run warms the card up
    started = time.monotonic()
    on_gpu = transcribe_wav(wav, gpu_model, TranscribeOptions(window_seconds=60.0))
    seconds = time.monotonic() - started

    heard = " ".join(norm(" ".join(w.text for w in on_gpu.words)))
    for phrase in KEY_PHRASES:
        assert phrase in heard, f"not heard on the graphics card: {phrase}"
    cpu, gpu = set(norm(" ".join(w.text for w in transcript.raw_words))), set(heard.split())
    assert len(cpu & gpu) / len(cpu | gpu) >= 0.9            # a word may differ here and there, not the substance
    assert all(a.start <= b.start for a, b in zip(on_gpu.words, on_gpu.words[1:]))
    assert seconds < on_gpu.duration / 10, f"took {seconds:.1f}s for {on_gpu.duration:.0f}s of audio"
