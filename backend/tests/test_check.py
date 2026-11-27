"""Claude's second look: every shortlisted moment is read again (and looked at, when frames go with it), and only
what holds up as the kind asked for becomes a clip. The command-line tool is never really called here."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from app.analysis import check, claude_review, focus
from app.analysis.check import Verdict
from app.analysis.claude_review import ClaudeUnavailable, Pick
from app.analysis.engine import HighlightOptions, highlights_from_picks
from app.transcription.sentences import build_sentences
from tests.helpers import make_audio, make_visual, script_to_words
from tests.test_scoring import FILLER, STORY

WORDS = script_to_words(FILLER + STORY)
SENTENCES = build_sentences(WORDS)
STORY_START = next(w.start for w in WORDS if w.text == "Can")
END = WORDS[-1].end
INSIDE = [s for s in SENTENCES if s.start >= STORY_START - 0.01]     # the story's own lines
OPTIONS = HighlightOptions(count=2, min_len=8, max_len=40, focus="funny")


def story(**changes) -> Pick:
    return Pick(**{"start": STORY_START, "end": END, "title": "He lost his phone in his own hand",
                   "hook": "45 minutes looking for my phone", "category": "funny", "score": 88.0,
                   "reason": "A complete story with a punchline.", **changes})


def settings_menu() -> Pick:
    return Pick(start=0.5, end=10.0, title="Settings", hook="", category="funny", score=60.0, reason="Calm.")


def judged(checks: list, prose: str = "") -> str:
    """What the command-line tool prints for a second look."""
    text = f"{prose}```json\n{json.dumps({'checks': checks})}\n```"
    return json.dumps({"type": "result", "is_error": False, "result": text})


def request(pictures: bool = False, kind: str = "funny") -> str:
    options = HighlightOptions(count=2, min_len=8, max_len=40, focus=kind)
    return check.build_prompt(SENTENCES, [story(), settings_menu()], options, title="Friday stream", creator="Kai",
                              game="GTA", loud_times=[20.0, 300.0], pictures=pictures)


# ---------------------------------------------------------------------------------------------- the frames
def test_frames_are_taken_closest_together_in_the_opening_seconds():
    times = check.frame_times(100.0, 130.0)

    assert len(times) == check.FRAMES and times == sorted(times)
    assert times[0] >= 100.0 and times[-1] <= 130.0
    assert sum(1 for t in times if t <= 103.0) == 3            # the opening is what makes people stay


def test_a_very_short_moment_still_gets_frames_spread_inside_it():
    times = check.frame_times(10.0, 13.0)

    assert len(set(times)) == check.FRAMES and all(10.0 <= t <= 13.0 for t in times)


@pytest.mark.ffmpeg
def test_each_moment_gets_one_sheet_of_frames(tiny_video: Path):
    (sheet,) = check.sheets(tiny_video, [Pick(start=0.2, end=2.8, title="a", hook="", category="funny", score=80.0,
                                              reason="")])

    with Image.open(io.BytesIO(sheet)) as image:
        assert image.format == "JPEG"
        assert image.size == (4 * check.TILE_WIDTH, 2 * round(check.TILE_WIDTH * 360 / 640))


def test_when_frames_cannot_be_taken_no_pictures_are_sent(tmp_path: Path):
    assert check.sheets(tmp_path / "gone.mp4", [story()]) == []


# --------------------------------------------------------------------------------------------- the request
def test_the_request_shows_each_moment_with_its_lines_its_times_and_the_kind_asked_for():
    prompt = request()
    funny = focus.get("funny")

    assert "Funny moments" in prompt and funny.proof in prompt and funny.not_this in prompt
    assert "Moment 1" in prompt and "Moment 2" in prompt and "He lost his phone in his own hand" in prompt
    assert "Can I tell you the dumbest thing" in prompt and f"[{STORY_START:.1f}]" in prompt   # times in seconds
    assert "Loud or busy at: 20.0" in prompt and "300.0" not in prompt          # only what falls inside a moment
    assert '"verdict"' in prompt and '"start"' in prompt and "never invent" in prompt.lower()
    assert "picture" not in prompt.lower() and "frames" not in prompt.lower()


def test_with_pictures_the_request_says_which_picture_belongs_to_which_moment():
    prompt = request(pictures=True)

    assert "picture 1 belongs to moment 1" in prompt and "frames" in prompt


def test_with_no_kind_asked_for_a_moment_only_has_to_be_worth_stopping_for():
    prompt = request(kind="best")

    assert "one kind" not in prompt and "stop scrolling" in prompt


def test_lines_just_before_a_moment_are_shown_so_a_missing_setup_can_be_pulled_in():
    late = story(start=INSIDE[1].start)
    prompt = check.build_prompt(SENTENCES, [late], OPTIONS, title="Friday stream", creator="Kai", game="",
                                loud_times=[], pictures=False)

    assert "(before the clip)" in prompt and INSIDE[0].text in prompt


# ---------------------------------------------------------------------------------------------- the answer
def test_verdicts_are_read_by_moment_number_and_kept_in_range():
    verdicts = check.parse_answer(judged([
        {"pick": 1, "verdict": "keep", "fit": 140, "reason": " The punchline lands. ", "start": 15.5,
         "hook": "Where was it?"},
        {"pick": 2, "verdict": "DROP", "fit": -5, "reason": "Nothing happens."},
        {"pick": 9, "verdict": "keep"},                     # no such moment
        {"pick": 1, "verdict": "drop"},                     # said twice: the first stands
        {"verdict": "keep"}, "nonsense"], prose="Here is my second look:\n"), 2)

    assert set(verdicts) == {1, 2}
    assert verdicts[1] == Verdict(keep=True, fit=100.0, reason="The punchline lands.", start=15.5,
                                  hook="Where was it?")
    assert verdicts[2] == Verdict(keep=False, fit=0.0, reason="Nothing happens.")


def test_a_verdict_that_is_neither_keep_nor_drop_leaves_the_moment_unchecked():
    assert check.parse_answer(judged([{"pick": 1, "verdict": "maybe"}, {"pick": 2, "verdict": "keep"}]), 2).keys() == {2}


@pytest.mark.parametrize("printed", ["not json at all", json.dumps({"is_error": True, "result": "usage limit reached"}),
                                     judged([]), judged([{"pick": 7, "verdict": "keep"}])])
def test_an_answer_with_no_usable_verdict_counts_as_no_second_look(printed: str):
    with pytest.raises(ClaudeUnavailable):
        check.parse_answer(printed, 2)


def test_asking_sends_the_pictures_with_the_words(monkeypatch):  # noqa: ANN001
    seen: dict = {}

    def fake_run(prompt, **kw):  # noqa: ANN001, ANN003, ANN202
        seen.update(prompt=prompt, model=kw.get("model"), pictures=list(kw.get("pictures") or []))
        return judged([{"pick": 1, "verdict": "keep", "fit": 70, "reason": "ok"}])

    monkeypatch.setattr(claude_review, "run_claude", fake_run)

    verdicts = check.ask("the request", 1, model="opus", pictures=[b"jpeg"])

    assert seen == {"prompt": "the request", "model": "opus", "pictures": [b"jpeg"]}
    assert verdicts[1].keep and verdicts[1].fit == 70


# ------------------------------------------------------------------------------------- what comes of it
def test_a_dropped_moment_is_left_out_with_its_reason_and_a_kept_one_takes_both_scores():
    kept, dropped = check.apply([story(), settings_menu()],
                                {1: Verdict(True, 80.0, "The punchline lands."), 2: Verdict(False, 10.0, "Nothing happens.")},
                                SENTENCES, OPTIONS, looked=False)

    assert [p.title for p in kept] == ["He lost his phone in his own hand"]
    assert kept[0].score == 84.0                                            # 88 at first, 80 on the second look
    assert kept[0].checked == {"fit": 80.0, "reason": "The punchline lands.", "looked": False, "moved": 0.0}
    assert [(p.title, why) for p, why in dropped] == [("Settings", "Nothing happens.")]


def test_a_moment_without_a_verdict_stays_exactly_as_it_was():
    kept, dropped = check.apply([story(), settings_menu()], {}, SENTENCES, OPTIONS, looked=True)

    assert kept == [story(), settings_menu()] and dropped == []


def test_the_start_moves_to_a_line_in_the_opening_third_and_nowhere_else():
    second, middle = INSIDE[1], INSIDE[1].start + (INSIDE[1].end - INSIDE[1].start) / 2
    assert second.start - STORY_START < (END - STORY_START) / 3 and middle - second.start > 0.5

    def moved_to(start: float, options: HighlightOptions = OPTIONS) -> Pick:
        (pick,), _ = check.apply([story()], {1: Verdict(True, 80.0, "ok", start=start)}, SENTENCES, options, looked=True)
        return pick

    onto = moved_to(second.start + 0.2)                                      # near enough: it means that line
    assert onto.start == pytest.approx(second.start)
    assert onto.checked["moved"] == pytest.approx(second.start - STORY_START, abs=0.01)
    assert moved_to(middle).start == STORY_START                             # the middle of a line: no
    assert moved_to(INSIDE[-1].start).start == STORY_START                   # far too late in the clip: no
    assert moved_to(second.start, HighlightOptions(count=2, min_len=END - STORY_START - 1, max_len=40,
                                                   focus="funny")).start == STORY_START   # too short then: no


def test_the_start_can_be_pulled_back_to_take_in_a_missing_setup_line():
    late = story(start=INSIDE[1].start)

    (pick,), _ = check.apply([late], {1: Verdict(True, 80.0, "ok", start=INSIDE[0].start)}, SENTENCES, OPTIONS,
                             looked=False)

    assert pick.start == pytest.approx(INSIDE[0].start) and pick.checked["moved"] < 0


def test_a_better_hook_line_goes_first_and_the_old_ones_stay_to_choose_from():
    first = story(hooks=["Where was it?"])

    (pick,), _ = check.apply([first], {1: Verdict(True, 80.0, "ok", hook="It was in his hand")}, SENTENCES, OPTIONS,
                             looked=False)

    assert pick.hook == "It was in his hand"
    assert pick.hooks == ["45 minutes looking for my phone", "Where was it?"]


def test_the_second_look_is_shown_on_the_clip():
    kept, _ = check.apply([story()], {1: Verdict(True, 80.0, "The punchline lands.")}, SENTENCES, OPTIONS, looked=True)

    (clip,) = highlights_from_picks(WORDS, SENTENCES, make_audio(END + 5), make_visual(END + 5), OPTIONS, kept,
                                    duration=END + 5)

    note = next(e for e in clip.evidence if e["dimension"] == "check")
    assert (note["verdict"], note["fit"], note["looked"]) == ("keep", 80.0, True)
    assert note["text"] == "Second look, with frames of the video: The punchline lands."
    assert clip.score == 84.0


def test_a_moved_start_is_said_on_the_clip():
    kept, _ = check.apply([story()], {1: Verdict(True, 80.0, "Tighter.", start=INSIDE[1].start)}, SENTENCES, OPTIONS,
                          looked=False)

    (clip,) = highlights_from_picks(WORDS, SENTENCES, make_audio(END + 5), make_visual(END + 5), OPTIONS, kept,
                                    duration=END + 5)

    note = next(e for e in clip.evidence if e["dimension"] == "check")
    assert note["text"].startswith("Second look: Tighter. It now opens ") and "later" in note["text"]
    assert clip.start == pytest.approx(INSIDE[1].start, abs=0.3)


# ------------------------------------------------------------------- seen when it was tried for real
def test_a_start_on_the_line_the_clip_opens_on_anyway_is_not_called_a_move():
    early = story(start=STORY_START - 0.7)       # Claude's own start, in the pause before the first line

    (pick,), _ = check.apply([early], {1: Verdict(True, 80.0, "ok", start=INSIDE[0].start)}, SENTENCES, OPTIONS,
                             looked=True)

    assert pick.start == early.start and pick.checked["moved"] == 0.0


def test_a_reason_that_repeats_its_verdict_is_read_without_it():
    verdicts = check.parse_answer(judged([
        {"pick": 1, "verdict": "keep", "reason": "Keep: the kindness is clear."},
        {"pick": 2, "verdict": "drop", "reason": "drop - it opens on a scolding."},
        {"pick": 3, "verdict": "keep", "reason": "Kept: tense quiet near a large creature."},
        {"pick": 4, "verdict": "drop", "reason": "Dropped: mostly bright scenery."},
        {"pick": 5, "verdict": "keep", "reason": "Keeps its build-up intact."}]), 5)

    assert [verdicts[n].reason for n in (1, 2, 3, 4, 5)] == [
        "The kindness is clear.", "It opens on a scolding.", "Tense quiet near a large creature.",
        "Mostly bright scenery.", "Keeps its build-up intact."]


# ---------------------------------------- a kind the picture carries: moments found from sound and picture
def action(start: float, score: float = 55.0):  # noqa: ANN201
    """A loud, busy moment as the local rules find one where almost nothing is said."""
    from app.analysis.engine import Highlight

    return Highlight(start=start, end=start + 20.0, score=score, subscores={"visual": 80.0}, confidence=0.38,
                     explanation="Sound peaks 17 dB above the surrounding level.",
                     evidence=[{"dimension": "emotion", "impact": 40.0, "time": start + 8, "quote": None,
                                "text": "Sound peaks 17 dB above the surrounding level"}],
                     category="action", title=f"Action at {start:.0f}", hook_text="", hook_options=[], hashtags=[],
                     description="", transcript_text="", rank=0, analysis_source="signals-only", peak_time=start + 8)


def test_loud_busy_moments_join_the_candidates_best_first_where_nothing_was_picked_yet():
    spoken = action(300.0, 90.0)
    spoken.analysis_source = "heuristic"                    # found from what is said: not one of these
    found = check.from_signals([action(100.0, 50.0), action(200.0, 70.0), spoken, action(400.0, 60.0),
                                action(STORY_START + 2.0, 99.0)],   # where Claude already picked
                               [story()], focus.get("horror"), room=2)

    assert [(p.start, p.category, p.title) for p in found] == [(200.0, "scare", ""), (400.0, "scare", "")]
    assert all(p.local is not None for p in found)


def test_such_a_moment_is_shown_as_having_no_title_and_claude_is_asked_to_name_it():
    seen = check.from_signals([action(200.0)], [], focus.get("horror"), room=2)
    options = HighlightOptions(count=2, min_len=8, max_len=40, focus="horror")
    prompt = check.build_prompt(SENTENCES, [story(), *seen], options, title="Friday stream", creator="Kai", game="",
                                loud_times=[208.0], pictures=True)
    without = check.build_prompt(SENTENCES, [story()], options, title="Friday stream", creator="Kai", game="",
                                 loud_times=[], pictures=True)

    assert "Moment 2: 200.0 to 220.0, found from sound and picture alone" in prompt
    assert "(nothing is said in it)" in prompt and "Loud or busy at: 208.0" in prompt
    assert '"title"' in prompt and '"title"' not in without


def test_a_title_for_such_a_moment_is_read_from_the_answer():
    verdicts = check.parse_answer(judged([{"pick": 1, "verdict": "keep", "fit": 90, "reason": "A clean chase.",
                                           "title": "  The dragon dives  "}]), 1)

    assert verdicts[1].title == "The dragon dives"


def test_a_kept_one_takes_claudes_title_and_reason_and_an_unjudged_one_is_left_out():
    first, second = check.from_signals([action(200.0, 60.0), action(400.0, 50.0)], [], focus.get("horror"), room=2)

    kept, dropped = check.apply([first, second], {1: Verdict(True, 90.0, "A clean chase.", title="The dragon dives",
                                                             hook="It came from above")},
                                SENTENCES, OPTIONS, looked=True)

    (pick,) = kept
    assert (pick.title, pick.reason, pick.hook, pick.score) == ("The dragon dives", "A clean chase.",
                                                                "It came from above", 75.0)
    assert dropped == []                                    # nobody judged the second: it is not a clip, nor a drop


def test_it_becomes_a_clip_over_its_own_stretch_with_what_was_measured_there():
    (seen,) = check.from_signals([action(200.0, 60.0)], [], focus.get("horror"), room=1)
    kept, _ = check.apply([seen], {1: Verdict(True, 90.0, "A clean chase.", title="The dragon dives")}, SENTENCES,
                          OPTIONS, looked=True)

    (clip,) = highlights_from_picks(WORDS, SENTENCES, make_audio(260.0), make_visual(260.0),
                                    HighlightOptions(count=2, min_len=8, max_len=40, focus="horror"), kept,
                                    duration=260.0)

    assert (clip.start, clip.end, clip.title, clip.analysis_source) == (200.0, 220.0, "The dragon dives", "claude")
    assert clip.subscores == {"visual": 80.0}
    texts = [e["text"] for e in clip.evidence]
    assert texts[0] == "Claude: A clean chase." and "Sound peaks 17 dB above the surrounding level" in texts
    assert texts.count("Claude: A clean chase.") == 1 and not [t for t in texts[1:] if "A clean chase" in t]
    seen = next(e for e in clip.evidence if e["dimension"] == "check")
    assert seen["looked"] and seen["text"] == ("Second look, with frames of the video: found from sound and picture, "
                                               "and kept once Claude had seen it.")


def test_the_local_rules_own_note_about_the_kind_does_not_stay_on_a_clip_claude_judged():
    found = action(200.0, 60.0)
    found.evidence.append({"dimension": "focus", "impact": 0.0, "time": None, "quote": None,
                           "text": "Asked for “Horror / scares”: loud, busy moments like this one are looked at first"})
    (seen,) = check.from_signals([found], [], focus.get("horror"), room=1)
    kept, _ = check.apply([seen], {1: Verdict(True, 90.0, "A clean chase.", title="The dragon dives")}, SENTENCES,
                          OPTIONS, looked=True)

    (clip,) = highlights_from_picks(WORDS, SENTENCES, make_audio(260.0), make_visual(260.0),
                                    HighlightOptions(count=2, min_len=8, max_len=40, focus="horror"), kept,
                                    duration=260.0)

    assert not [e for e in clip.evidence if "looked at first" in e["text"]]
