"""Videos with a little talk and a lot of action (gameplay with sparse commentary, films).

Speech still decides wherever there is speech. Where almost nothing is said, the loud and busy moments are
offered too, marked as resting on sound and picture only, and never by cutting a sentence in half."""
from __future__ import annotations

import pytest

from app.analysis.engine import Highlight, HighlightOptions, find_highlights
from app.transcription.sentences import Word, build_sentences
from tests.helpers import make_audio, make_visual, script_to_words
from tests.test_scoring import FILLER, STORY

DURATION = 240.0
OPTIONS = dict(min_len=10, max_len=25)
SHOUT_AT = 122.0          # "No! No! No!" in the middle of the first burst of action
FIRST_ACTION = (119.0, 127.0)
SECOND_ACTION = (169.0, 176.0)
LOUD_TALK = (2.0, 8.0)    # the commentator is loud here, but only reading out settings


class Gameplay:
    """Talk for the first ~40 s (filler, then a story), then two bursts of action with almost no words."""

    def __init__(self) -> None:
        talk = script_to_words(FILLER + STORY)
        self.story_start = next(w.start for w in talk if w.text == "Can")
        self.talk_end = talk[-1].end
        shout = [Word("No!", SHOUT_AT, SHOUT_AT + 0.3, 0.9), Word("No!", SHOUT_AT + 0.4, SHOUT_AT + 0.7, 0.9),
                 Word("No!", SHOUT_AT + 0.8, SHOUT_AT + 1.2, 0.9)]
        self.words = talk + shout
        self.sentences = build_sentences(self.words)
        self.audio = make_audio(DURATION, loud=[(*LOUD_TALK, 15.0), (120.0, 126.0, 16.0), (170.0, 175.0, 14.0)])
        self.visual = make_visual(DURATION, busy=[(*LOUD_TALK, 0.3), (*FIRST_ACTION, 0.4), (*SECOND_ACTION, 0.3)])

    def run(self, **options) -> list[Highlight]:
        return find_highlights(self.words, self.sentences, self.audio, self.visual,
                               HighlightOptions(**{"count": 4, **OPTIONS, **options}), duration=DURATION)


@pytest.fixture(scope="module")
def game() -> Gameplay:
    return Gameplay()


def spoken(h: Highlight) -> bool:
    return h.analysis_source != "signals-only"


def covers(h: Highlight, span: tuple[float, float]) -> bool:
    return h.start <= span[0] + 2.0 and h.end >= span[1] - 2.0


def test_a_video_with_little_talk_also_gets_its_loud_busy_moments(game: Gameplay):
    highlights = game.run()

    talk = [h for h in highlights if spoken(h)]
    action = [h for h in highlights if not spoken(h)]
    assert talk and all(h.end <= game.talk_end + 1.0 for h in talk)          # the story is still found by its words
    assert any(h.start <= game.story_start + 1.0 and h.end >= game.talk_end - 6.0 for h in talk)
    assert len(action) == 2
    assert any(covers(h, (120.0, 126.0)) for h in action) and any(covers(h, (170.0, 175.0)) for h in action)
    for h in action:
        assert h.confidence <= 0.45 and h.category == "action"
        assert OPTIONS["min_len"] - 0.6 <= h.end - h.start <= OPTIONS["max_len"] + 0.6
        assert "sound and picture" in h.explanation


def test_ranks_run_from_one_in_order_of_score_across_both_kinds(game: Gameplay):
    highlights = game.run()

    assert [h.rank for h in highlights] == list(range(1, len(highlights) + 1))
    assert [h.score for h in highlights] == sorted((h.score for h in highlights), reverse=True)


def test_a_shout_in_the_middle_of_the_action_is_kept_whole_and_captioned(game: Gameplay):
    burst = next(h for h in game.run() if not spoken(h) and h.start < SHOUT_AT < h.end)

    assert burst.transcript_text == "No! No! No!"
    assert burst.start <= SHOUT_AT - 0.1 and burst.end >= SHOUT_AT + 1.2 + 0.1
    assert any("Almost nothing is said" in item["text"] for item in burst.evidence)


def test_an_action_moment_never_starts_or_ends_in_the_middle_of_a_sentence(game: Gameplay):
    """(Spoken moments have their own rule: they may drop a leading "um, okay, so".)"""
    action = [h for h in game.run(count=6, min_len=8, max_len=40) if not spoken(h)]
    assert action
    for h in action:
        for sentence in game.sentences:
            assert not (sentence.start + 0.05 < h.start < sentence.end - 0.05), f"starts inside “{sentence.text}”"
            assert not (sentence.start + 0.05 < h.end < sentence.end - 0.05), f"ends inside “{sentence.text}”"


def test_loud_talk_is_never_offered_as_an_action_moment(game: Gameplay):
    """Being loud while reading out the settings menu is still filler. Where people are talking, the
    words decide; the action finder must not bring it back in through the side door."""
    for h in game.run(count=8):
        if not spoken(h):
            assert h.start >= game.talk_end, f"an action moment was cut out of the talk at {h.start:.0f}s"


def test_action_moments_only_fill_places_that_speech_left_empty(game: Gameplay):
    only_one = game.run(count=1)
    assert len(only_one) == 1 and spoken(only_one[0])                       # the best spoken moment keeps its place

    everything = game.run(count=8)
    for a in everything:
        for b in everything:
            if a is not b:
                assert min(a.end, b.end) - max(a.start, b.start) <= 1.0, "two suggestions overlap"


def test_the_minimum_score_applies_to_action_moments_too(game: Gameplay):
    assert all(spoken(h) for h in game.run(min_score=90))
    assert all(h.score >= 40 for h in game.run(min_score=40))


def test_quiet_stretches_with_a_still_picture_are_not_suggested():
    """Silence in the commentary is not a moment by itself."""
    words = script_to_words(FILLER + STORY)

    highlights = find_highlights(words, build_sentences(words), make_audio(DURATION), make_visual(DURATION),
                                 HighlightOptions(count=5, **OPTIONS), duration=DURATION)

    assert highlights and all(spoken(h) for h in highlights)


def test_a_stretch_too_short_for_a_clip_is_left_alone():
    """Four seconds of noise between two long stretches of talk cannot make a ten-second clip."""
    first = script_to_words(FILLER + STORY)
    gap_start = first[-1].end
    second = script_to_words(FILLER + STORY, start=gap_start + 4.0)
    words = first + second
    total = second[-1].end + 5.0
    audio = make_audio(total, loud=[(gap_start + 0.5, gap_start + 3.5, 16.0)])
    visual = make_visual(total, busy=[(gap_start + 0.5, gap_start + 3.5, 0.4)])

    highlights = find_highlights(words, build_sentences(words), audio, visual,
                                 HighlightOptions(count=6, **OPTIONS), duration=total)

    assert all(spoken(h) for h in highlights)


# ------------------------------------------------------------- the rule itself, with nothing else in the way
def action_only(words, sentences, audio, visual, duration, **options):  # noqa: ANN001, ANN201
    """The action finder alone: no spoken moment has been chosen, so nothing blocks any place."""
    from app.analysis import engine

    opts = HighlightOptions(**{"count": 6, **OPTIONS, **options})
    return engine._action_highlights(audio, visual, opts, duration, (), None, "", "", count=opts.count,
                                     words=words, sentences=sentences)


def test_where_people_are_mostly_talking_the_action_finder_stays_out(game: Gameplay):
    found = action_only(game.words, game.sentences, game.audio, game.visual, DURATION)

    assert len(found) == 2                                                   # the two bursts after the talk
    assert all(h.start >= game.talk_end for h in found), [(h.start, h.end) for h in found]


def test_a_burst_squeezed_between_two_long_stretches_of_talk_cannot_become_a_clip():
    first = script_to_words(FILLER + STORY)
    gap_start = first[-1].end
    second = script_to_words(FILLER + STORY, start=gap_start + 4.0)
    words = first + second
    total = second[-1].end + 5.0
    audio = make_audio(total, loud=[(gap_start + 0.5, gap_start + 3.5, 16.0)])
    visual = make_visual(total, busy=[(gap_start + 0.5, gap_start + 3.5, 0.4)])

    assert action_only(words, build_sentences(words), audio, visual, total) == []


# ------------------------------------------------------------------------ a kind of moment was asked for
def test_asked_for_gameplay_the_loud_busy_moments_come_first_even_though_there_is_talk(game: Gameplay):
    plain, wanted = game.run(count=2), game.run(count=2, focus="gameplay")

    assert any(spoken(h) for h in plain)                      # without a kind, the words decide first
    assert [spoken(h) for h in wanted] == [False, False]
    assert covers(wanted[0], FIRST_ACTION) or covers(wanted[1], FIRST_ACTION)


def test_asked_for_a_kind_every_local_pick_says_whether_it_is_one(game: Gameplay):
    from app.analysis import focus

    funny = focus.get("funny")
    for h in game.run(focus="funny"):
        note = next(e for e in h.evidence if e["dimension"] == "focus")
        if spoken(h):
            assert note["impact"] == (focus.LOCAL_BONUS if h.category in funny.categories else -focus.LOCAL_PENALTY)
        assert "Funny moments" in note["text"]


def test_with_no_kind_asked_for_the_local_picks_carry_no_such_note(game: Gameplay):
    assert not [e for h in game.run() for e in h.evidence if e["dimension"] == "focus"]
