"""The windowing, checkpoint and resume logic of transcription, with a stand-in for the speech model."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from app.errors import JobCancelled
from app.transcription.sentences import Word
from app.transcription.whisper_engine import (SAMPLE_RATE, TranscribeOptions, WindowResult,
                                              collapse_repeated_segments, is_noise_token, split_point,
                                              transcribe_wav)
from tests.test_audio_features import tone, write_wav


class FakeModel:
    """Returns one word per whole second of the audio it is given, timed relative to that audio."""

    def __init__(self, language: str = "en", fail_on_call: int | None = None) -> None:
        self.calls: list[tuple[int, str | None]] = []
        self.language = language
        self.fail_on_call = fail_on_call

    def __call__(self, audio: np.ndarray, language: str | None, on_fraction=None, should_cancel=None) -> WindowResult:
        self.calls.append((audio.size, language))
        if self.fail_on_call is not None and len(self.calls) == self.fail_on_call:
            raise RuntimeError("model crashed")
        seconds = audio.size // SAMPLE_RATE
        words = [Word(f"w{k}", k + 0.1, k + 0.6, 0.9) for k in range(seconds)]
        if on_fraction:
            on_fraction(1.0)
        return WindowResult(words=words, language=self.language, language_probability=0.99)


@pytest.fixture()
def wav_25s(tmp_path: Path) -> Path:
    return write_wav(tmp_path / "audio.wav", tone(25, 0.1))


OPTS = TranscribeOptions(window_seconds=10.0)


def test_split_point_lands_in_the_quiet_gap_near_the_end():
    samples = np.concatenate([tone(14.0, 0.2), tone(0.6, 0.0005), tone(5.4, 0.2)])

    cut = split_point(samples, search_seconds=10.0)

    assert 14.0 * SAMPLE_RATE <= cut <= 14.6 * SAMPLE_RATE


def test_split_point_stays_inside_the_search_range():
    samples = np.concatenate([tone(1.0, 0.0005), tone(19.0, 0.2)])  # the only quiet part is far too early

    assert split_point(samples, search_seconds=5.0) >= 15.0 * SAMPLE_RATE


def test_short_audio_is_transcribed_in_one_window(tmp_path: Path):
    model = FakeModel()
    result = transcribe_wav(write_wav(tmp_path / "a.wav", tone(4, 0.1)), model, OPTS)

    assert len(model.calls) == 1
    assert [w.text for w in result.words] == ["w0", "w1", "w2", "w3"]


def test_every_sample_is_transcribed_exactly_once(wav_25s: Path):
    model = FakeModel()
    result = transcribe_wav(wav_25s, model, OPTS)

    assert len(model.calls) >= 3
    assert sum(n for n, _ in model.calls) == 25 * SAMPLE_RATE
    assert result.windows == len(model.calls)


def test_word_times_are_shifted_to_their_place_in_the_whole_recording(wav_25s: Path):
    result = transcribe_wav(wav_25s, FakeModel(), OPTS)

    starts = [w.start for w in result.words]
    assert starts == sorted(starts)
    assert starts[0] == pytest.approx(0.1)
    assert starts[-1] > 20.0
    assert all(w.end <= 25.0 for w in result.words)


def test_the_language_found_in_the_first_window_is_used_for_the_rest(wav_25s: Path):
    model = FakeModel(language="de")
    result = transcribe_wav(wav_25s, model, OPTS)

    assert model.calls[0][1] is None
    assert all(language == "de" for _, language in model.calls[1:])
    assert result.language == "de"


def test_a_fixed_language_is_passed_to_every_window(wav_25s: Path):
    model = FakeModel()
    transcribe_wav(wav_25s, model, TranscribeOptions(window_seconds=10.0, language="en"))

    assert all(language == "en" for _, language in model.calls)


def test_progress_rises_to_one(wav_25s: Path):
    seen: list[float] = []
    transcribe_wav(wav_25s, FakeModel(), OPTS, on_progress=seen.append)

    assert seen == sorted(seen) and seen[-1] == pytest.approx(1.0)


def test_an_interrupted_run_resumes_without_redoing_finished_windows(wav_25s: Path, tmp_path: Path):
    checkpoint = tmp_path / "transcript.partial.json"
    clean = transcribe_wav(wav_25s, FakeModel(), OPTS)

    crashing = FakeModel(fail_on_call=3)
    with pytest.raises(RuntimeError):
        transcribe_wav(wav_25s, crashing, OPTS, checkpoint_path=checkpoint, signature="small.en|cpu")
    assert checkpoint.exists()

    resumed_model = FakeModel()
    resumed = transcribe_wav(wav_25s, resumed_model, OPTS, checkpoint_path=checkpoint, signature="small.en|cpu")

    assert len(resumed_model.calls) == clean.windows - 2          # the two finished windows are not redone
    assert resumed.resumed_windows == 2
    assert [(w.text, w.start) for w in resumed.words] == [(w.text, w.start) for w in clean.words]


def test_a_checkpoint_made_with_other_settings_is_ignored(wav_25s: Path, tmp_path: Path):
    checkpoint = tmp_path / "transcript.partial.json"
    with pytest.raises(RuntimeError):
        transcribe_wav(wav_25s, FakeModel(fail_on_call=2), OPTS, checkpoint_path=checkpoint, signature="small.en|cpu")

    model = FakeModel()
    result = transcribe_wav(wav_25s, model, OPTS, checkpoint_path=checkpoint, signature="large-v3|cuda")

    assert result.resumed_windows == 0
    assert sum(n for n, _ in model.calls) == 25 * SAMPLE_RATE


def test_the_checkpoint_is_removed_after_a_successful_run(wav_25s: Path, tmp_path: Path):
    checkpoint = tmp_path / "transcript.partial.json"
    transcribe_wav(wav_25s, FakeModel(), OPTS, checkpoint_path=checkpoint, signature="s")

    assert not checkpoint.exists()


def test_cancelling_stops_between_windows_and_keeps_the_checkpoint(wav_25s: Path, tmp_path: Path):
    checkpoint = tmp_path / "transcript.partial.json"
    model = FakeModel()

    with pytest.raises(JobCancelled):
        transcribe_wav(wav_25s, model, OPTS, checkpoint_path=checkpoint, signature="s",
                       should_cancel=lambda: len(model.calls) >= 1)

    assert len(model.calls) == 1
    assert json.loads(checkpoint.read_text(encoding="utf-8"))["next_sample"] > 0


def test_noise_tokens_are_recognised():
    assert is_noise_token("[Music]") and is_noise_token("(applause)") and is_noise_token("♪")
    assert not is_noise_token("Hello") and not is_noise_token("(not") and not is_noise_token("3.5")


def test_a_looping_hallucination_is_collapsed_but_normal_repetition_is_kept():
    looped = ["Thanks for watching.", "Thanks for watching.", "Thanks for watching.", "Thanks for watching.", "Bye."]
    assert collapse_repeated_segments(looped) == [0, 1, 4]            # indexes of the segments to keep
    assert collapse_repeated_segments(["No!", "No!", "No!", "No!"]) == [0, 1, 2, 3]   # one-word shouts are real
    assert collapse_repeated_segments(["Hello there.", "How are you?"]) == [0, 1]


def test_hyphen_and_punctuation_fragments_join_the_word_before_them():
    from app.transcription.whisper_engine import merge_fragments

    words = [Word("Open", 1.0, 1.3, 0.9), Word("-world", 1.3, 1.5, 0.7), Word("games", 1.5, 1.9, 0.9),
             Word(",", 1.9, 1.95, 0.5), Word("10", 2.0, 2.2, 0.8), Word("-hour", 2.2, 2.5, 0.9)]

    merged = merge_fragments(words)

    assert [w.text for w in merged] == ["Open-world", "games,", "10-hour"]
    assert (merged[0].start, merged[0].end) == (1.0, 1.5)
    assert merged[0].prob == pytest.approx(0.7)       # as confident as its least certain part


def test_a_leading_fragment_with_nothing_before_it_is_kept():
    from app.transcription.whisper_engine import merge_fragments

    assert [w.text for w in merge_fragments([Word("-world", 0.0, 0.2, 0.9)])] == ["-world"]
