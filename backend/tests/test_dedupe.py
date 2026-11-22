from __future__ import annotations

import pytest

from app.analysis.dedupe import ExistingClip, select_distinct, text_similarity, time_overlap
from app.analysis.lexicon import content_tokens
from app.analysis.presets import DIMENSIONS
from app.analysis.scoring import Scored


def cand(start: float, end: float, score: float, text: str = "") -> Scored:
    return Scored(i=0, j=0, start=start, end=end, first_word=0, score=score,
                  subscores={d: 60.0 for d in DIMENSIONS}, confidence=0.6, category="funny", evidence=[],
                  explanation="", peak_sentence=0, tokens=frozenset(content_tokens(text or f"unique{start}")))


def test_time_overlap_is_measured_against_the_shorter_clip():
    assert time_overlap(0, 10, 5, 30) == pytest.approx(0.5)
    assert time_overlap(0, 10, 20, 30) == 0.0
    assert time_overlap(0, 10, 2, 6) == pytest.approx(1.0)


def test_text_similarity_is_the_shared_share_of_words():
    a = frozenset("phone couch car fridge flashlight".split())
    b = frozenset("phone couch car fridge torch".split())
    assert text_similarity(a, b) == pytest.approx(4 / 6)
    assert text_similarity(a, frozenset()) == 0.0


def test_overlapping_candidates_keep_only_the_best_one():
    picked = select_distinct([cand(10, 40, 70), cand(15, 45, 88), cand(12, 38, 60)], count=5)

    assert [(c.start, c.end) for c in picked] == [(15, 45)]


def test_distinct_moments_are_all_kept_in_score_order():
    picked = select_distinct([cand(10, 40, 70), cand(100, 130, 88), cand(200, 230, 79)], count=5)

    assert [c.start for c in picked] == [100, 200, 10]


def test_count_limits_how_many_are_returned():
    picked = select_distinct([cand(i * 100, i * 100 + 30, 50 + i) for i in range(8)], count=3)
    assert len(picked) == 3
    assert [c.score for c in picked] == sorted((c.score for c in picked), reverse=True)


def test_the_same_story_told_twice_is_only_suggested_once():
    story = "I spent forty five minutes looking for my phone and it was in my hand the whole time"
    picked = select_distinct([cand(10, 40, 85, story), cand(900, 930, 80, story + " honestly"),
                              cand(400, 430, 70, "open world games are getting way too big")], count=5)

    assert [c.start for c in picked] == [10, 400]


def test_every_selected_clip_gets_a_uniqueness_score():
    story = "I spent forty five minutes looking for my phone"
    related = "I spent forty five minutes looking for my keys under the couch at home today"
    picked = select_distinct([cand(10, 40, 85, story), cand(400, 430, 80, related)], count=5)

    assert picked[0].subscores["uniqueness"] == 100
    assert 40 < picked[1].subscores["uniqueness"] < 100


def test_moments_that_already_exist_as_clips_are_not_suggested_again():
    existing = [ExistingClip(start=100, end=130, tokens=frozenset())]
    picked = select_distinct([cand(102, 128, 95), cand(300, 330, 70)], count=5, existing=existing)

    assert [c.start for c in picked] == [300]
