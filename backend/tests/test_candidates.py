from __future__ import annotations

import pytest

from app.analysis.candidates import WindowOptions, enumerate_windows, refine_bounds
from app.transcription.sentences import Word, build_sentences
from tests.helpers import find, transcript

LINES = [
    ("Welcome back everybody.", 0.5),
    ("Um, okay, so here is my hot take.", 0.4),
    ("Open world games are getting way too big.", 0.4),
    ("Nobody finishes them.", 0.4),
    ("I would rather play a perfect ten hour game.", 0.4),
    ("Bigger is not better.", 1.5),
    ("Anyway let me check the map.", 0.5),
]


def test_windows_respect_the_requested_length_range():
    words, sentences = transcript(LINES)
    opts = WindowOptions(min_len=6, max_len=12)

    windows = list(enumerate_windows(sentences, opts))

    assert windows
    for i, j in windows:
        assert i <= j
        assert 6 <= sentences[j].end - sentences[i].start <= 12


def test_no_windows_when_the_video_is_shorter_than_the_minimum():
    words, sentences = transcript(LINES[:2])
    assert list(enumerate_windows(sentences, WindowOptions(min_len=60, max_len=90))) == []


def test_bounds_sit_on_the_first_and_last_word_with_a_small_margin():
    words, sentences = transcript(LINES)
    i, j = find(sentences, "Open world"), find(sentences, "Bigger")
    opts = WindowOptions(min_len=5, max_len=30)

    bounds = refine_bounds(i, j, words, sentences, opts, duration=words[-1].end + 1)

    first, last = words[sentences[i].w0], words[sentences[j].w1 - 1]
    assert first.start - opts.pre_roll - 1e-6 <= bounds.start <= first.start
    assert last.end <= bounds.end <= last.end + opts.post_roll + 1e-6


def test_leading_filler_words_are_trimmed_from_the_start():
    words, sentences = transcript(LINES)
    i = find(sentences, "Um, okay")
    here = next(w for w in words if w.text == "here")

    bounds = refine_bounds(i, find(sentences, "Bigger"), words, sentences, WindowOptions(min_len=5, max_len=30),
                           duration=words[-1].end + 1)

    # "Um, okay," is dropped; "so" stays because it is not followed by a pause or more filler
    so = next(w for w in words if w.text == "so")
    assert bounds.start == pytest.approx(so.start - 0.15, abs=0.2)
    assert bounds.start > words[sentences[i].w0 + 1].end - 1e-6      # after "okay,"
    assert bounds.start <= here.start
    assert bounds.first_word == sentences[i].w0 + 2


def test_start_never_reaches_back_into_the_previous_word():
    # the previous sentence ends 0.04 s before this one starts
    words = [Word("Stop.", 0.0, 0.50, 0.9), Word("Now", 0.54, 0.8, 0.9), Word("watch", 0.85, 1.1, 0.9),
             Word("this.", 1.15, 1.5, 0.9)]
    sentences = build_sentences(words)

    bounds = refine_bounds(1, 1, words, sentences, WindowOptions(min_len=0, max_len=30), duration=5.0)

    assert bounds.start > 0.50
    assert bounds.start <= 0.54


def test_end_never_runs_into_the_next_word():
    words = [Word("Watch", 0.0, 0.3, 0.9), Word("this.", 0.35, 0.7, 0.9), Word("Okay", 0.78, 1.0, 0.9),
             Word("next.", 1.05, 1.4, 0.9)]
    sentences = build_sentences(words)

    bounds = refine_bounds(0, 0, words, sentences, WindowOptions(min_len=0, max_len=30), duration=5.0)

    assert 0.7 <= bounds.end < 0.78


def test_bounds_stay_inside_the_video():
    words = [Word("Hello", 0.02, 0.3, 0.9), Word("there.", 0.35, 0.9, 0.9)]
    sentences = build_sentences(words)

    bounds = refine_bounds(0, 0, words, sentences, WindowOptions(min_len=0, max_len=30), duration=1.0)

    assert bounds.start >= 0.0
    assert bounds.end <= 1.0
