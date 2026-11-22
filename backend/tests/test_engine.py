"""The whole highlight engine on a synthetic stream that mixes filler with four planted moments."""
from __future__ import annotations

import re

import numpy as np
import pytest

from app.analysis.audio import HOP, AudioFeatures
from app.analysis.dedupe import ExistingClip
from app.analysis.engine import HighlightOptions, find_highlights
from app.analysis.feedback import FeedbackProfile
from app.analysis.lexicon import content_tokens
from app.analysis.presets import DIMENSIONS, effective_weights
from app.transcription.sentences import build_sentences
from tests.helpers import make_audio, make_visual, script_to_words

# (name, worth clipping?, [(line, pause after, shouted?)])
SECTIONS = [
    ("intro", False, [("Okay. So, um, welcome back to the stream, everybody.", 0.7, False),
                      ("Give me a second. I am just going to fix my audio settings here.", 1.4, False),
                      ("Yeah. Let me check the settings menu. Okay, I think that is fine now.", 1.8, False)]),
    ("clutch", True, [("Wait. There is one enemy left, and I have exactly one health point.", 0.5, False),
                      ("Do not move. Do not even breathe.", 1.3, False),
                      ("No way! No way! I got him! That was a headshot through the wall!", 0.3, True),
                      ("Let's go! That is the craziest clutch I have ever hit in my life!", 0.5, True),
                      ("Chat is going absolutely insane right now.", 1.6, False)]),
    ("loot", False, [("Alright. Um, so anyway, let me just pick up the loot here.", 0.9, False),
                     ("I need some ammo. And some bandages. Okay.", 3.2, False),
                     ("Hmm. Let me check the map.", 2.0, False)]),
    ("story", True, [("Can I tell you the dumbest thing that happened to me yesterday?", 0.4, False),
                     ("Go ahead.", 0.4, False),
                     ("I spent forty five minutes looking for my phone.", 0.3, False),
                     ("I checked the couch, I checked the car, I even checked the fridge.", 0.6, False),
                     ("And the whole time, I was using the flashlight on my phone to look for it.", 0.5, False),
                     ("Ha ha ha! You were holding it the entire time? That is incredible.", 1.8, False)]),
    ("chat", False, [("So yeah. Um, thanks for the follow, by the way. Thank you.", 1.2, False),
                     ("Let me read some chat messages. Hello to everyone joining.", 2.2, False)]),
    ("take", True, [("Here is my hot take, and I know people will be mad about this.", 0.4, False),
                    ("Open world games are getting way too big. Nobody finishes them.", 0.4, False),
                    ("I would rather play a perfect ten hour game than a boring hundred hour game.", 0.4, False),
                    ("Bigger is not better.", 0.6, False),
                    ("That is actually a terrible opinion. Exploring a huge map is the whole point.", 0.4, False),
                    ("No, you are wrong, and I will die on this hill.", 1.8, False)]),
    ("fail", True, [("Okay, watch this jump. I have done this a thousand times. Easy.", 1.4, False),
                    ("No! No, no, no, no! I fell off the map! I lost everything!", 0.3, True),
                    ("Three hours of progress, gone!", 0.6, True),
                    ("That might be the worst fail I have ever seen.", 1.8, False)]),
    ("outro", False, [("Alright, um, I think that is it for today.", 0.8, False),
                      ("Thanks for watching, everyone. See you tomorrow. Bye.", 1.5, False)]),
]


class Stream:
    def __init__(self) -> None:
        self.words, self.ranges, loud = [], {}, []
        t = 0.0
        for name, _worthy, lines in SECTIONS:
            begin = t
            for text, pause, shouted in lines:
                chunk = script_to_words([(text, pause)], start=t)
                if shouted:
                    loud.append((chunk[0].start, chunk[-1].end, 15.0))
                self.words.extend(chunk)
                t = chunk[-1].start + 1 / 3.0 + pause
            self.ranges[name] = (begin, t)
        self.duration = t
        self.sentences = build_sentences(self.words)
        self.audio = make_audio(self.duration, loud=loud)
        busy = [(*self.ranges[n], 0.25) for n in ("clutch", "fail")]
        self.visual = make_visual(self.duration, busy=busy)
        self.worthy = [n for n, w, _ in SECTIONS if w]
        self.filler = [n for n, w, _ in SECTIONS if not w]

    def inside(self, clip, names) -> float:
        """Share of the clip's length that lies inside the named sections."""
        covered = 0.0
        for n in names:
            a, b = self.ranges[n]
            covered += max(0.0, min(clip.end, b) - max(clip.start, a))
        return covered / (clip.end - clip.start)

    def run(self, **kw):
        options = HighlightOptions(**{"count": 4, "min_len": 8, "max_len": 30, **kw})
        return find_highlights(self.words, self.sentences, self.audio, self.visual, options,
                               duration=self.duration, video_title="Sunday stream", creator="TestStreamer")


@pytest.fixture(scope="module")
def stream() -> Stream:
    return Stream()


def test_finds_all_four_planted_moments_and_none_of_the_filler(stream: Stream):
    highlights = stream.run()

    assert len(highlights) == 4
    for name in stream.worthy:
        assert any(stream.inside(h, [name]) >= 0.6 for h in highlights), f"missed the {name} moment"
    for h in highlights:
        assert stream.inside(h, stream.filler) < 0.35


def test_highlights_are_ranked_best_first_and_do_not_overlap(stream: Stream):
    highlights = stream.run()

    assert [h.score for h in highlights] == sorted((h.score for h in highlights), reverse=True)
    assert [h.rank for h in highlights] == [1, 2, 3, 4]
    ordered = sorted(highlights, key=lambda h: h.start)
    for a, b in zip(ordered, ordered[1:]):
        assert a.end <= b.start + 1.0


def test_clip_lengths_stay_inside_the_requested_range(stream: Stream):
    for h in stream.run(min_len=8, max_len=30):
        assert 8 - 0.6 <= h.end - h.start <= 30 + 0.6


def test_a_high_minimum_score_returns_fewer_clips_instead_of_padding(stream: Stream):
    everything = stream.run(count=8, min_score=0)
    strict = stream.run(count=8, min_score=70)

    assert len(strict) < len(everything)
    assert all(h.score >= 70 for h in strict)


def test_each_highlight_is_ready_to_show(stream: Stream):
    for h in stream.run():
        assert set(h.subscores) == set(DIMENSIONS)
        assert 0 < len(h.title) <= 80
        assert h.hook_text and h.hook_text in h.hook_options
        assert 1 <= len(h.hook_options) <= 4
        assert h.category
        assert h.explanation and len(h.evidence) >= 3
        assert 0 < h.confidence < 1
        assert h.hashtags and all(tag.startswith("#") and " " not in tag for tag in h.hashtags)
        assert h.transcript_text
        assert h.analysis_source == "heuristic"


def test_titles_and_hooks_only_use_words_that_were_actually_said(stream: Stream):
    """No invented dialogue: every suggested hook line is a quote from the clip itself."""
    def norm(text: str) -> list[str]:
        return re.findall(r"[a-z0-9']+", text.lower())

    for h in stream.run():
        spoken = " ".join(norm(h.transcript_text))
        for option in h.hook_options:
            assert " ".join(norm(option)) in spoken, f"hook not found in the clip: {option!r}"


def test_the_transcript_text_matches_the_words_inside_the_clip(stream: Stream):
    for h in stream.run():
        inside = [w.text for w in stream.words if w.start >= h.start - 0.01 and w.end <= h.end + 0.01]
        assert h.transcript_text == " ".join(inside)


def test_a_video_with_no_speech_falls_back_to_action_peaks_with_low_confidence():
    duration = 300.0
    audio = make_audio(duration, loud=[(60, 63, 16.0), (150, 154, 18.0), (240, 242, 14.0)])
    visual = make_visual(duration, busy=[(58, 66, 0.3), (148, 157, 0.35), (238, 245, 0.3)])

    highlights = find_highlights([], [], audio, visual, HighlightOptions(count=3, min_len=10, max_len=20),
                                 duration=duration)

    assert len(highlights) == 3
    peaks = [61.5, 152.0, 241.0]
    for peak in peaks:
        assert any(h.start < peak < h.end for h in highlights)
    for h in highlights:
        assert h.confidence <= 0.45
        assert h.analysis_source == "signals-only"
        assert 10 - 0.6 <= h.end - h.start <= 20 + 0.6


def test_nothing_is_returned_for_silence_with_a_still_picture():
    duration = 120.0
    silent = AudioFeatures(hop=HOP, duration=duration, rms_db=np.full(1200, -70, np.float32),
                           delta_db=np.zeros(1200, np.float32), flux=np.zeros(1200, np.float32),
                           silence_db=-50.0, speech_level_db=-70.0)

    assert find_highlights([], [], silent, make_visual(duration, base=0.0),
                           HighlightOptions(count=3, min_len=10, max_len=20), duration=duration) == []


def test_a_second_run_does_not_suggest_moments_that_already_exist_as_clips(stream: Stream):
    first = stream.run()
    top = first[0]
    existing = [ExistingClip(start=top.start, end=top.end, tokens=frozenset(content_tokens(top.transcript_text)))]

    again = find_highlights(stream.words, stream.sentences, stream.audio, stream.visual,
                            HighlightOptions(count=4, min_len=8, max_len=30), duration=stream.duration,
                            existing=existing)

    for h in again:
        overlap = max(0.0, min(h.end, top.end) - max(h.start, top.start))
        assert overlap < 0.5 * min(h.end - h.start, top.end - top.start)


def test_feedback_about_missing_context_raises_the_weight_of_clarity():
    neutral = effective_weights("auto", None)
    profile = FeedbackProfile.from_labels(["too_much_context"] * 4)
    adjusted = effective_weights("auto", profile)

    assert adjusted["clarity"] > neutral["clarity"]
    assert sum(adjusted.values()) == pytest.approx(1.0)
    assert sum(neutral.values()) == pytest.approx(1.0)


def test_feedback_can_favour_or_disfavour_a_category():
    profile = FeedbackProfile.from_labels([("good", "funny")] * 3 + [("not_entertaining", "hot_take")] * 3)

    assert profile.category_bias["funny"] > 0
    assert profile.category_bias["hot_take"] < 0
    assert -15 <= min(profile.category_bias.values()) and max(profile.category_bias.values()) <= 15


def test_cancellation_stops_the_search(stream: Stream):
    from app.errors import JobCancelled

    with pytest.raises(JobCancelled):
        find_highlights(stream.words, stream.sentences, stream.audio, stream.visual,
                        HighlightOptions(count=4, min_len=8, max_len=30), duration=stream.duration,
                        should_cancel=lambda: True)


def test_hook_lines_end_at_a_natural_stop_not_mid_phrase(stream: Stream):
    """A hook is a whole sentence or a clause up to a comma, never "…the craziest clutch I have ever"."""
    def norm(token: str) -> str:
        return re.sub(r"[^a-z0-9']", "", token.lower())

    for h in stream.run():
        clip_words = [w.text for w in stream.words if w.start >= h.start - 0.01 and w.end <= h.end + 0.01]
        normalised = [norm(t) for t in clip_words]
        for option in h.hook_options:
            wanted = [norm(t) for t in option.split()]
            at = next(k for k in range(len(normalised)) if normalised[k:k + len(wanted)] == wanted)
            last_token = clip_words[at + len(wanted) - 1]
            assert last_token[-1] in ".,!?;:", f"hook stops mid-phrase: {option!r}"
            assert len(wanted) <= 14


def test_every_planted_moment_offers_a_choice_of_hooks(stream: Stream):
    for h in stream.run():
        assert len(h.hook_options) >= 2, h.hook_options


def test_heuristic_confidence_is_never_reported_as_high(stream: Stream):
    """Without a language-model pass the estimate rests on cues and signals, so it stays below 'high'."""
    for h in stream.run(count=8):
        assert h.confidence < 0.75


def test_the_default_hook_is_the_title_line_not_just_the_opening_that_is_about_to_be_spoken(stream: Stream):
    """An on-screen hook that only repeats the first sentence adds nothing: prefer the line the clip is named after."""
    clutch = next(h for h in stream.run() if h.category == "hype")

    assert clutch.hook_text.lower().startswith("that is the craziest clutch")
    assert clutch.hook_text in clutch.hook_options
