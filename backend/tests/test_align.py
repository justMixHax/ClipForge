"""Word timing correction: speech models stretch words into neighbouring silence; the audio says where
the word really is."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.transcription.align import refine_word_times, speech_envelope
from app.transcription.sentences import Word
from tests.test_audio_features import SR, tone, write_wav


def burst_wav(path: Path, bursts: list[tuple[float, float]], total: float) -> Path:
    """Near-silence with a tone wherever a 'word' is spoken."""
    samples = tone(total, 0.0004)
    for start, end in bursts:
        a, b = int(start * SR), int(end * SR)
        samples[a:b] = tone((b - a) / SR, 0.2)
    return write_wav(path, samples)


def refine(tmp_path: Path, bursts, words, total: float = 6.0) -> list[Word]:
    envelope = speech_envelope(burst_wav(tmp_path / "a.wav", bursts, total))
    return refine_word_times(words, envelope)


def test_the_envelope_separates_speech_from_silence(tmp_path: Path):
    envelope = speech_envelope(burst_wav(tmp_path / "a.wav", [(1.0, 2.0)], 4.0))

    assert envelope.voiced(1.2, 1.8) == pytest.approx(1.0)
    assert envelope.voiced(2.5, 3.5) == pytest.approx(0.0)


def test_a_word_stretched_back_into_the_pause_before_it_is_pulled_forward(tmp_path: Path):
    words = [Word("better.", 1.0, 1.4, 0.9), Word("Bigger", 1.6, 2.9, 0.9), Word("is", 2.9, 3.1, 0.9)]
    out = refine(tmp_path, [(1.0, 1.4), (2.5, 3.1)], words)

    assert out[1].start == pytest.approx(2.5, abs=0.06)
    assert out[1].end == pytest.approx(2.9, abs=0.02)


def test_a_word_stretched_forward_into_the_pause_after_it_is_shortened(tmp_path: Path):
    words = [Word("times", 1.0, 2.2, 0.9), Word("easy", 2.5, 2.9, 0.9)]
    out = refine(tmp_path, [(1.0, 1.4), (2.5, 2.9)], words)

    assert out[0].start == pytest.approx(1.0, abs=0.03)
    assert out[0].end == pytest.approx(1.4, abs=0.06)


def test_well_timed_words_are_left_alone(tmp_path: Path):
    words = [Word("watch", 1.0, 1.3, 0.9), Word("this", 1.3, 1.55, 0.9), Word("jump.", 1.55, 2.0, 0.9)]
    out = refine(tmp_path, [(1.0, 2.0)], words)

    for before, after in zip(words, out):
        assert after.start == pytest.approx(before.start, abs=0.03)
        assert after.end == pytest.approx(before.end, abs=0.03)


def test_a_zero_length_word_gets_a_real_duration(tmp_path: Path):
    words = [Word("ahead.", 1.0, 1.4, 0.9), Word("I", 2.5, 2.5, 0.9), Word("spent", 2.6, 3.0, 0.9)]
    out = refine(tmp_path, [(1.0, 1.4), (2.4, 3.0)], words)

    assert 0.05 <= out[1].end - out[1].start <= 0.4
    assert out[0].end <= out[1].start and out[1].end <= out[2].start


def test_a_word_placed_in_silence_moves_next_to_the_word_it_belongs_with(tmp_path: Path):
    """'So' was timed in the gap after the previous sentence, seconds before it was really said."""
    words = [Word("incredible.", 1.0, 1.4, 0.9), Word("So", 1.5, 2.3, 0.2), Word("yeah.", 3.3, 3.7, 0.9)]
    out = refine(tmp_path, [(1.0, 1.4), (3.0, 3.7)], words)

    assert 2.9 <= out[1].start < out[1].end <= 3.3


def test_words_keep_their_order_and_never_overlap(tmp_path: Path):
    words = [Word("one", 0.5, 1.9, 0.9), Word("two", 1.2, 3.1, 0.9), Word("three", 2.8, 4.4, 0.9)]
    out = refine(tmp_path, [(1.0, 1.3), (2.0, 2.3), (3.5, 3.9)], words)

    assert all(w.end > w.start for w in out)
    assert all(a.end <= b.start + 1e-6 for a, b in zip(out, out[1:]))


def test_text_and_confidence_are_untouched(tmp_path: Path):
    words = [Word("Bigger", 1.6, 2.9, 0.42)]
    out = refine(tmp_path, [(2.5, 2.9)], words)

    assert (out[0].text, out[0].prob) == ("Bigger", 0.42)


def test_refinement_is_skipped_when_the_audio_has_no_quiet_parts(tmp_path: Path):
    """Constant loud game audio under the voice: there is no silence to measure against, so trust the model."""
    envelope = speech_envelope(write_wav(tmp_path / "a.wav", tone(5, 0.3)))
    words = [Word("Bigger", 1.6, 2.9, 0.9)]

    assert refine_word_times(words, envelope) == words
    assert isinstance(envelope.level_db, np.ndarray)
