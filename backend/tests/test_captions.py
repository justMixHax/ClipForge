from __future__ import annotations

import re

import pytest

from app.editing.captions import (PRESETS, CaptionWord, ass_color, ass_time, build_ass, build_srt, build_vtt,
                                  censor, group_words, mark_emphasis, resolve_style, words_for_clip)
from app.transcription.sentences import Word


def cw(text: str, start: float, end: float, **kw) -> CaptionWord:
    return CaptionWord(text=text, start=start, end=end, **kw)


def spoken(text: str, start: float = 0.0, step: float = 0.3) -> list[CaptionWord]:
    return [cw(t, round(start + i * step, 3), round(start + i * step + step * 0.9, 3)) for i, t in enumerate(text.split())]


def events(ass: str, style: str = "Caption") -> list[dict]:
    out = []
    for line in ass.splitlines():
        if line.startswith("Dialogue:"):
            parts = line[len("Dialogue:"):].strip().split(",", 9)
            if parts[3] == style:
                out.append({"start": parts[1], "end": parts[2], "text": parts[9]})
    return out


def plain(text: str) -> str:
    return re.sub(r"\{[^}]*\}", "", text).replace("\\N", " ")


# ---------------------------------------------------------------------------------------------- timing
def test_source_words_are_shifted_into_clip_time_and_outside_words_dropped():
    source = [Word("before", 8.0, 8.4), Word("Wait.", 10.2, 10.5), Word("There", 11.0, 11.3), Word("after", 21.0, 21.3)]

    words = words_for_clip(source, [(10.0, 20.0)])

    assert [(w.text, w.start, w.end) for w in words] == [("Wait.", 0.2, 0.5), ("There", 1.0, 1.3)]


def test_words_follow_the_cuts_when_a_stretch_is_removed():
    source = [Word("one", 10.5, 10.9), Word("gone", 14.5, 15.0), Word("two", 17.0, 17.4)]

    words = words_for_clip(source, [(10.0, 14.0), (16.0, 20.0)])   # 2 s removed in the middle

    assert [(w.text, w.start, w.end) for w in words] == [("one", 0.5, 0.9), ("two", 5.0, 5.4)]


def test_a_word_cut_by_the_clip_edge_is_clamped_not_given_negative_time():
    words = words_for_clip([Word("edge", 9.9, 10.3)], [(10.0, 20.0)])

    assert words and words[0].start == 0.0 and words[0].end == pytest.approx(0.3)


# -------------------------------------------------------------------------------------------- grouping
def test_groups_respect_the_words_per_line_limit():
    groups = group_words(spoken("one two three four five six seven"), resolve_style({"preset": "opus", "max_words": 3}))

    assert [len(g) for g in groups] == [3, 3, 1]


def test_a_new_line_starts_after_a_sentence_ends_or_a_pause():
    words = spoken("No way.") + spoken("I got him", start=1.0) + spoken("after a pause", start=4.0)
    groups = group_words(words, resolve_style({"preset": "opus", "max_words": 4}))

    assert [" ".join(w.text for w in g) for g in groups] == ["No way.", "I got him", "after a pause"]


def test_long_words_do_not_share_a_line_beyond_the_character_limit():
    groups = group_words(spoken("absolutely unbelievable circumstances"), resolve_style({"max_words": 3, "max_chars": 16}))

    assert all(len(" ".join(w.text for w in g)) <= 16 or len(g) == 1 for g in groups)
    assert len(groups) == 3


# -------------------------------------------------------------------------------------------- emphasis
def test_numbers_and_strong_words_are_emphasised_but_ordinary_words_are_not():
    words = spoken("I spent 45 minutes and it was the craziest thing ever")
    mark_emphasis(words)
    flagged = {w.text for w in words if w.emph}

    assert "45" in flagged and "craziest" in flagged
    assert not flagged & {"I", "and", "it", "was", "the"}


def test_emphasis_stays_rare_so_it_keeps_its_meaning():
    words = spoken("insane crazy unbelievable never ever worst best literally insane crazy never worst")
    mark_emphasis(words)

    assert sum(w.emph for w in words) <= max(1, len(words) // 3)


def test_manually_set_emphasis_is_never_overridden():
    words = [cw("plain", 0, 0.3, emph=True, locked=True), cw("45", 0.3, 0.6, emph=False, locked=True)]
    mark_emphasis(words)

    assert [w.emph for w in words] == [True, False]


# ------------------------------------------------------------------------------------------- censoring
def test_swear_words_are_starred_keeping_first_and_last_letter_and_case():
    assert censor("fucking") == "f*****g"
    assert censor("Shit!") == "S**t!"
    assert censor("class assess bass") == "class assess bass"


# ------------------------------------------------------------------------------------------ ASS output
def test_colours_and_times_use_the_ass_formats():
    assert ass_color("#FFE600") == "&H0000E6FF"
    assert ass_color("#000000", opacity=0.5) == "&H80000000"
    assert ass_time(0) == "0:00:00.00"
    assert ass_time(75.239) == "0:01:15.24"
    assert ass_time(3661.5) == "1:01:01.50"


def test_the_script_is_sized_to_the_output_video():
    ass, _ = build_ass(spoken("hello there world"), resolve_style({}), 1080, 1920)

    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert "Style: Caption," in ass


def test_highlight_style_has_one_event_per_word_with_only_that_word_highlighted():
    style = resolve_style({"preset": "opus", "max_words": 3, "highlight": "#00FF00"})
    ass, _ = build_ass(spoken("one two three four"), style, 1080, 1920)
    ev = events(ass)

    assert len(ev) == 4
    assert [plain(e["text"]) for e in ev] == ["ONE TWO THREE", "ONE TWO THREE", "ONE TWO THREE", "FOUR"]
    green = "\\c&H00FF00&"   # inline colour tags are written blue-green-red, without alpha
    assert ev[1]["text"].count(green) == 1
    assert ev[1]["text"].index(green) < ev[1]["text"].index("TWO") < ev[1]["text"].index("THREE")


def test_caption_events_are_in_order_and_never_overlap():
    ass, _ = build_ass(spoken("one two three four five six seven eight"), resolve_style({}), 1080, 1920)
    ev = events(ass)

    assert all(a["end"] <= b["start"] for a, b in zip(ev, ev[1:]))
    assert all(e["start"] < e["end"] for e in ev)


def test_karaoke_style_has_one_event_per_line_with_timed_fills():
    style = resolve_style({"preset": "karaoke", "max_words": 4})
    words = spoken("one two three four")
    ass, _ = build_ass(words, style, 1080, 1920)
    ev = events(ass)

    assert len(ev) == 1
    fills = [int(n) for n in re.findall(r"\\kf(\d+)", ev[0]["text"])]
    assert len(fills) == 4
    assert sum(fills) == pytest.approx((words[-1].end - words[0].start) * 100, abs=3)


def test_minimal_style_shows_the_line_without_highlighting_or_capitals():
    style = resolve_style({"preset": "minimal"})
    ass, _ = build_ass(spoken("Hello there my friend"), style, 1080, 1920)
    ev = events(ass)

    assert len(ev) == 1
    assert plain(ev[0]["text"]) == "Hello there my friend"
    assert "\\c&H" not in ev[0]["text"]


def test_word_by_word_style_shows_a_single_word_at_a_time():
    ass, _ = build_ass(spoken("one two three"), resolve_style({"preset": "word"}), 1080, 1920)

    assert [plain(e["text"]) for e in events(ass)] == ["ONE", "TWO", "THREE"]


def test_every_preset_builds_and_is_readable_on_a_phone():
    for name in PRESETS:
        style = resolve_style({"preset": name})
        ass, report = build_ass(spoken("this is a test of the caption presets"), style, 1080, 1920)
        assert events(ass), name
        assert style.size >= 54, f"{name} is too small to read on a phone"
        assert report["overflow"] == []


def test_captions_sit_inside_the_safe_area_of_a_vertical_video():
    for position in (0.0, 0.5, 0.99):
        ass, report = build_ass(spoken("hello there"), resolve_style({"position": position}), 1080, 1920)
        y = float(re.search(r"\\pos\(\d+(?:\.\d+)?,(\d+(?:\.\d+)?)\)", events(ass)[0]["text"]).group(1))
        assert 1920 * 0.10 <= y <= 1920 * 0.80
        assert report["position_y"] == pytest.approx(y)


def test_a_line_too_wide_for_the_frame_is_shrunk_to_fit():
    style = resolve_style({"preset": "opus", "max_words": 3, "max_chars": 60, "size": 110})
    words = spoken("supercalifragilistic extraordinarily unbelievable")
    ass, report = build_ass(words, style, 1080, 1920)

    assert report["shrunk"] >= 1
    assert re.search(r"\\fs(\d+)", events(ass)[0]["text"])
    assert int(re.search(r"\\fs(\d+)", events(ass)[0]["text"]).group(1)) < 110
    assert report["max_width"] <= 1080 * 0.9 + 1


def test_text_that_looks_like_formatting_codes_is_shown_literally():
    ass, _ = build_ass([cw("{\\b1}hack", 0, 0.5), cw("ok", 0.5, 1.0)], resolve_style({"preset": "minimal"}), 1080, 1920)
    text = events(ass)[0]["text"]

    assert "{\\b1}" not in text
    assert "hack" in text


def test_swearing_is_masked_on_screen_only_when_the_style_asks_for_it():
    words = spoken("this is fucking insane")
    masked, _ = build_ass(words, resolve_style({"censor": True, "preset": "minimal"}), 1080, 1920)
    verbatim, _ = build_ass(words, resolve_style({"censor": False, "preset": "minimal"}), 1080, 1920)

    assert "f*****g" in events(masked)[0]["text"] and "fucking" not in events(masked)[0]["text"]
    assert "fucking" in events(verbatim)[0]["text"]


def test_a_hook_title_is_added_as_its_own_layer_for_its_duration():
    ass, _ = build_ass(spoken("one two three"), resolve_style({}), 1080, 1920, hook="He did not expect this at all",
                       hook_seconds=4.0, clip_seconds=20.0)
    hook = events(ass, "Hook")

    assert len(hook) == 1
    assert (hook[0]["start"], hook[0]["end"]) == ("0:00:00.00", "0:00:04.00")
    assert plain(hook[0]["text"]) == "HE DID NOT EXPECT THIS AT ALL"


def test_hidden_words_are_not_shown():
    words = [cw("keep", 0, 0.3), cw("skip", 0.3, 0.6, hidden=True), cw("this", 0.6, 0.9)]
    ass, _ = build_ass(words, resolve_style({"preset": "minimal"}), 1080, 1920)

    assert plain(events(ass)[0]["text"]) == "keep this"


# ------------------------------------------------------------------------------------------ SRT / VTT
def test_srt_is_numbered_timed_and_verbatim():
    srt = build_srt(spoken("This is fucking great. And here is another line for you."))
    blocks = srt.strip().split("\n\n")

    assert blocks[0].splitlines()[0] == "1"
    assert re.fullmatch(r"\d\d:\d\d:\d\d,\d\d\d --> \d\d:\d\d:\d\d,\d\d\d", blocks[0].splitlines()[1])
    assert "fucking" in srt                     # files stay verbatim; only burned-in text is masked
    assert all(len(line) <= 42 for b in blocks for line in b.splitlines()[2:])


def test_vtt_has_the_header_and_dot_decimals():
    vtt = build_vtt(spoken("Hello there. General greeting."))

    assert vtt.startswith("WEBVTT\n\n")
    assert re.search(r"\d\d:\d\d:\d\d\.\d\d\d --> \d\d:\d\d:\d\d\.\d\d\d", vtt)
    assert "Hello there." in vtt


def test_caption_words_round_trip_through_json():
    word = cw("45", 1.0, 1.4, emph=True, locked=True)
    assert CaptionWord.from_dict(word.to_dict()) == word


def test_capital_letter_styles_drop_trailing_full_stops_and_commas_but_keep_questions_and_exclamations():
    words = spoken("I got him. Really? Yes! Okay, fine")
    ass, _ = build_ass(words, resolve_style({"preset": "opus", "max_words": 1, "max_chars": 40}), 1080, 1920)
    shown = [plain(e["text"]) for e in events(ass)]

    assert shown == ["I", "GOT", "HIM", "REALLY?", "YES!", "OKAY", "FINE"]


def test_sentence_case_styles_keep_their_punctuation():
    ass, _ = build_ass(spoken("I got him. Okay, fine."), resolve_style({"preset": "minimal"}), 1080, 1920)

    assert [plain(e["text"]) for e in events(ass)] == ["I got him.", "Okay, fine."]
