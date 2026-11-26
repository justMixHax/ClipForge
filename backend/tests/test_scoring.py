"""Behaviour of the explainable scorer. Each test states one rule the product promises."""
from __future__ import annotations

from app.analysis.presets import DIMENSIONS
from app.analysis.scoring import ScoreOptions, ScoringContext
from tests.helpers import find, make_audio, make_visual, transcript

FILLER = [
    ("Um okay so let me check the settings menu here.", 0.4),
    ("Yeah I think the audio is fine now.", 0.4),
    ("Let me just pick up the loot and check the map.", 0.4),
    ("Okay so anyway let me read some chat messages.", 1.2),
]
STORY = [
    ("Can I tell you the dumbest thing that happened to me yesterday?", 0.4),
    ("I spent forty five minutes looking for my phone.", 0.3),
    ("I checked the couch, I checked the car, I even checked the fridge.", 0.6),
    ("And the whole time, I was using the flashlight on my phone to look for it.", 0.5),
    ("Ha ha ha! You were holding it the entire time?", 0.3),
    ("That is incredible!", 1.5),
]
CLUTCH = [
    ("Wait. There is one enemy left, and I have exactly one health point.", 0.5),
    ("Do not move. Do not even breathe.", 1.3),
    ("No way! No way! I got him! That was a headshot through the wall!", 0.3),
    ("Let's go! That is the craziest clutch I have ever hit in my life!", 1.5),
]


def context(lines, audio=None, visual="default", preset="auto", **kw):
    words, sentences = transcript(lines, **kw)
    duration = words[-1].end + 2.0
    if audio is None:
        audio = make_audio(duration)
    if visual == "default":
        visual = make_visual(duration)
    return ScoringContext(words, sentences, audio, visual, ScoreOptions(preset=preset)), sentences


def test_a_loud_moment_with_nothing_said_does_not_outrank_a_real_story():
    words, sentences = transcript(FILLER + STORY)
    story_first = find(sentences, "Can I tell you")
    filler_end = sentences[story_first - 1].end
    audio = make_audio(words[-1].end + 2, loud=[(0.0, filler_end, 22.0)])  # the filler is shouted
    ctx = ScoringContext(words, sentences, audio, make_visual(words[-1].end + 2), ScoreOptions())

    filler = ctx.score(0, story_first - 1)
    story = ctx.score(story_first, len(sentences) - 1)

    assert story.score > filler.score + 10


def test_loudness_alone_cannot_carry_emotion_or_entertainment():
    words, sentences = transcript(FILLER)
    audio = make_audio(words[-1].end + 2, loud=[(0.0, words[-1].end, 25.0)])
    ctx = ScoringContext(words, sentences, audio, make_visual(words[-1].end + 2), ScoreOptions())

    filler = ctx.score(0, len(sentences) - 1)

    assert filler.subscores["emotion"] <= 50
    assert filler.subscores["entertainment"] <= 50


def test_a_strong_opening_hook_beats_a_filler_opening():
    body = STORY[1:]
    hooked, s1 = context([STORY[0]] + body)
    mumbled, s2 = context([("Um so yeah anyway I guess there was this thing.", 0.4)] + body)

    strong = hooked.score(0, len(s1) - 1).subscores["hook"]
    weak = mumbled.score(0, len(s2) - 1).subscores["hook"]

    assert strong > weak + 15


def test_a_clip_that_leans_on_earlier_context_loses_clarity():
    body = [("He called me at three in the morning.", 0.3), ("He had locked himself out of his own car.", 0.3),
            ("With the engine still running!", 1.2)]
    dependent, s1 = context([("And that's why he said it, like I told you earlier.", 0.3)] + body)
    standalone, s2 = context([("My brother did the funniest thing last night.", 0.3)] + body)

    unclear = dependent.score(0, len(s1) - 1).subscores["clarity"]
    clear = standalone.score(0, len(s2) - 1).subscores["clarity"]

    assert clear > unclear + 15


def test_ending_right_after_the_payoff_beats_trailing_on_for_many_seconds():
    tail = [("Um anyway so let me check the map here for a second.", 0.4)] * 5
    ctx, sentences = context(STORY + tail)
    punchline = find(sentences, "That is incredible")

    tight = ctx.score(0, punchline)
    trailing = ctx.score(0, len(sentences) - 1)

    assert tight.subscores["payoff"] > trailing.subscores["payoff"] + 10
    assert tight.score > trailing.score


def test_dead_air_in_the_middle_hurts_pacing():
    tight, s1 = context([("I checked the couch and the car.", 0.3), ("Then I checked the fridge as well.", 0.3),
                         ("It was in my hand the entire time!", 1.0)])
    gappy, s2 = context([("I checked the couch and the car.", 6.5), ("Then I checked the fridge as well.", 0.3),
                         ("It was in my hand the entire time!", 1.0)])

    assert tight.score(0, len(s1) - 1).subscores["pacing"] > gappy.score(0, len(s2) - 1).subscores["pacing"] + 15


def test_every_score_has_all_twelve_dimensions_within_0_to_100():
    ctx, sentences = context(STORY)
    scored = ctx.score(0, len(sentences) - 1)

    assert set(scored.subscores) == set(DIMENSIONS)
    assert len(DIMENSIONS) == 12
    assert all(0 <= v <= 100 for v in scored.subscores.values())
    assert 0 <= scored.score <= 100
    assert 0 < scored.confidence < 1


def test_the_explanation_cites_real_lines_and_times_from_the_clip():
    ctx, sentences = context(FILLER + STORY)
    first = find(sentences, "Can I tell you")
    scored = ctx.score(first, len(sentences) - 1, explain=True)
    clip_text = " ".join(s.text for s in sentences[first:]).lower()

    assert scored.explanation
    assert len(scored.evidence) >= 3
    for item in scored.evidence:
        assert item["dimension"] in DIMENSIONS
        assert item["text"]
        if item.get("time") is not None:
            assert scored.start - 0.5 <= item["time"] <= scored.end + 0.5
        if item.get("quote"):
            assert item["quote"].rstrip("…").strip().lower() in clip_text
    assert any(item.get("quote") for item in scored.evidence)
    assert any(item["impact"] > 0 for item in scored.evidence)


def test_poorly_heard_speech_lowers_confidence_and_subtitle_readability():
    clear, s1 = context(STORY, prob=0.95)
    murky, s2 = context(STORY, prob=0.40)

    good = clear.score(0, len(s1) - 1)
    bad = murky.score(0, len(s2) - 1)

    assert bad.confidence < good.confidence
    assert bad.subscores["subtitles"] < good.subscores["subtitles"] - 15


def test_very_fast_speech_lowers_subtitle_readability():
    normal, s1 = context(STORY, wps=3.0)
    rapid, s2 = context(STORY, wps=5.6)

    assert rapid.score(0, len(s2) - 1).subscores["subtitles"] < normal.score(0, len(s1) - 1).subscores["subtitles"] - 15


def test_the_gaming_preset_values_gameplay_action_more_than_the_podcast_preset():
    words, sentences = transcript(CLUTCH)
    duration = words[-1].end + 2
    audio = make_audio(duration, loud=[(sentences[find(sentences, "No way")].start, words[-1].end, 14.0)])
    visual = make_visual(duration, busy=[(0.0, duration, 0.3)])

    gaming = ScoringContext(words, sentences, audio, visual, ScoreOptions(preset="gaming"))
    podcast = ScoringContext(words, sentences, audio, visual, ScoreOptions(preset="podcast"))

    assert gaming.score(0, len(sentences) - 1).score > podcast.score(0, len(sentences) - 1).score + 2


def test_without_visual_data_the_visual_score_is_neutral_and_confidence_drops():
    with_picture, s1 = context(STORY)
    without_picture, s2 = context(STORY, visual=None)

    seen = with_picture.score(0, len(s1) - 1)
    blind = without_picture.score(0, len(s2) - 1)

    assert blind.subscores["visual"] == 50
    assert blind.confidence < seen.confidence


def test_a_pause_right_before_a_loud_payoff_is_rewarded():
    def clutch(pause: float):
        lines = [CLUTCH[0], ("Do not move. Do not even breathe.", pause), CLUTCH[2], CLUTCH[3]]
        words, sentences = transcript(lines)
        shout = sentences[find(sentences, "No way")].start
        audio = make_audio(words[-1].end + 2, loud=[(shout, words[-1].end, 14.0)])
        ctx = ScoringContext(words, sentences, audio, make_visual(words[-1].end + 2), ScoreOptions())
        return ctx.score(0, len(sentences) - 1).subscores["payoff"]

    assert clutch(pause=1.3) > clutch(pause=0.1) + 5


def test_stream_housekeeping_scores_low_overall():
    lines = [("So yeah, thanks for the follow, by the way.", 0.5), ("Thank you for the sub as well.", 0.5),
             ("Let me read some chat messages.", 0.5), ("Hello to everyone joining the stream.", 0.5),
             ("Give me a second, I am going to fix my audio settings.", 1.0)]
    ctx, sentences = context(lines)

    assert ctx.score(0, len(sentences) - 1).score < 50


def test_a_planted_highlight_scores_clearly_above_average():
    ctx, sentences = context(STORY)
    assert ctx.score(0, len(sentences) - 1).score >= 70


def test_category_reflects_what_happens_in_the_clip():
    story_ctx, s1 = context(STORY)
    clutch_ctx, s2 = context(CLUTCH)

    assert story_ctx.score(0, len(s1) - 1).category == "funny"
    assert clutch_ctx.score(0, len(s2) - 1).category == "hype"


TAKE_END = [("That is actually a terrible opinion.", 0.4), ("No, you are wrong, and I will die on this hill.", 1.8)]
FAIL = [
    ("Okay, watch this jump. I have done this a thousand times. Easy.", 1.4),
    ("No! No, no, no, no! I fell off the map! I lost everything!", 0.3),
    ("Three hours of progress, gone!", 0.6),
    ("That might be the worst fail I have ever seen.", 1.8),
]


def clutch_context():
    words, sentences = transcript(CLUTCH)
    shout = sentences[find(sentences, "No way")].start
    audio = make_audio(words[-1].end + 2, loud=[(shout, words[-1].end, 14.0)])
    return ScoringContext(words, sentences, audio, make_visual(words[-1].end + 2), ScoreOptions()), sentences


def test_starting_on_the_reaction_without_its_setup_loses_clarity_and_ranks_lower():
    ctx, sentences = clutch_context()
    reaction = find(sentences, "No way")

    whole = ctx.score(0, len(sentences) - 1)
    reaction_only = ctx.score(reaction, len(sentences) - 1)

    assert whole.subscores["clarity"] > reaction_only.subscores["clarity"] + 8
    assert whole.score > reaction_only.score


def test_ending_while_the_reaction_is_still_going_hurts_payoff():
    ctx, sentences = clutch_context()
    first_shout = find(sentences, "No way")

    cut_short = ctx.score(0, first_shout)
    complete = ctx.score(0, len(sentences) - 1)

    assert complete.subscores["payoff"] > cut_short.subscores["payoff"] + 8


def test_starting_on_the_tail_of_the_previous_topic_is_penalised():
    words, sentences = transcript(TAKE_END + FAIL)
    duration = words[-1].end + 2
    ctx = ScoringContext(words, sentences, make_audio(duration), make_visual(duration), ScoreOptions())
    fail_start = find(sentences, "Okay, watch this jump")

    straddling = ctx.score(find(sentences, "No, you are wrong"), len(sentences) - 1)
    clean = ctx.score(fail_start, len(sentences) - 1)

    assert clean.subscores["clarity"] > straddling.subscores["clarity"] + 15
    assert clean.subscores["hook"] > straddling.subscores["hook"]
    assert clean.score > straddling.score


def test_the_payoff_score_leaves_headroom_so_better_endings_can_still_win():
    ctx, sentences = context(STORY)
    assert ctx.score(0, len(sentences) - 1).subscores["payoff"] < 100


def test_a_picture_that_moves_in_the_first_seconds_hooks_better_than_a_still_one():
    # What stops the scroll is something happening on screen in the first seconds, not only the words.
    words, sentences = transcript(STORY)
    duration = words[-1].end + 2.0
    opening = (words[0].start - 0.15, words[0].start + 3.0, 0.6)
    moving = ScoringContext(words, sentences, make_audio(duration), make_visual(duration, busy=[opening]),
                            ScoreOptions(preset="auto"))
    still = ScoringContext(words, sentences, make_audio(duration), make_visual(duration, base=0.001),
                           ScoreOptions(preset="auto"))

    busy_hook = moving.score(0, len(sentences) - 1, explain=True)
    still_hook = still.score(0, len(sentences) - 1, explain=True)

    assert busy_hook.subscores["hook"] > still_hook.subscores["hook"]
    assert any(e["dimension"] == "hook" and "picture" in e["text"].lower() and "first seconds" in e["text"].lower()
               for e in busy_hook.evidence)
    assert any(e["dimension"] == "hook" and "picture" in e["text"].lower() and e["impact"] < 0
               for e in still_hook.evidence)
