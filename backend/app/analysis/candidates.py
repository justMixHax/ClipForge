"""Where a clip may start and end.

A candidate is a run of whole sentences, so a cut never lands mid-sentence. The exact in and out points
are then placed just outside the first and last word: a short breath before and after when there is room,
never reaching into the neighbouring word, and skipping "um, okay, so" at the very start.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from ..transcription.sentences import Sentence, Word
from .lexicon import leading_filler_count

FILLER_LOOKAHEAD = 10  # only the first few words are checked for filler


@dataclass(frozen=True, slots=True)
class WindowOptions:
    min_len: float
    max_len: float
    pre_roll: float = 0.15
    post_roll: float = 0.35


@dataclass(frozen=True, slots=True)
class Bounds:
    start: float
    end: float
    first_word: int  # index of the first word kept (after trimming filler)
    last_word: int   # index of the last word kept


def enumerate_windows(sentences: list[Sentence], opts: WindowOptions) -> Iterator[tuple[int, int]]:
    """Every run of consecutive sentences whose length is inside the requested range."""
    n = len(sentences)
    for i in range(n):
        start = sentences[i].start
        for j in range(i, n):
            length = sentences[j].end - start
            if length > opts.max_len:
                break
            if length >= opts.min_len:
                yield i, j


def refine_bounds(i: int, j: int, words: list[Word], sentences: list[Sentence], opts: WindowOptions,
                  duration: float | None = None) -> Bounds:
    w0, w_end = sentences[i].w0, sentences[j].w1
    # skip empty tokens at either edge
    while w0 < w_end - 1 and not words[w0].text.strip():
        w0 += 1
    last = w_end - 1
    while last > w0 and not words[last].text.strip():
        last -= 1

    lookahead = [words[k].text for k in range(w0, min(last + 1, w0 + FILLER_LOOKAHEAD))]
    first = w0 + leading_filler_count(lookahead)

    start = words[first].start - opts.pre_roll
    if first > 0:
        start = max(start, words[first - 1].end + 0.02)
    start = max(0.0, min(start, words[first].start))

    end = words[last].end + opts.post_roll
    if last + 1 < len(words):
        end = min(end, words[last + 1].start - 0.03)
    end = max(end, words[last].end)
    if duration is not None:
        end = min(end, duration)
    return Bounds(start=round(start, 3), end=round(end, 3), first_word=first, last_word=last)
