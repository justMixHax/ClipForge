"""Prompt edit: you describe what you want changed in a clip, Claude turns it into changes ClipForge can make, and
the clip is rendered again. Claude is never really called here: a stand-in answers instead. Whatever Claude answers
is checked before anything is saved, what ClipForge cannot do is said rather than faked, and one press undoes it."""
from __future__ import annotations

import copy
import json

import pytest
from fastapi.testclient import TestClient

from app import db
from app.analysis import claude_review
from app.editing import prompt_edit
from app.editing.captions import CaptionWord
from app.errors import ClipForgeError
from app.jobs import pipeline
from app.models import Clip, Job
from app.transcription.sentences import Word
from tests.test_api import client, clip, upload, uploaded  # noqa: F401  (fixtures shared with the main API tests)

FONTS = ["Anton", "Arial", "Arial Black", "Impact"]
WORDS = [Word("Okay", 10.0, 10.4), Word("ladies", 10.5, 11.0), Word("welcome", 11.2, 11.8), Word("back", 11.9, 12.3),
         Word("number", 20.0, 20.5), Word("one", 20.6, 21.0), Word("goodbye", 38.0, 38.8)]


def state(**overrides) -> dict:  # noqa: ANN003
    """A clip as prompt_edit sees it: its cut, its edit settings and its words on screen."""
    base = {"start": 10.0, "end": 40.0, "hook_text": "He did not expect that", "title": "First date", "captions": None,
            "cover_text": "", "cover_word": "", "cover_time": None, "transcript_text": "Okay ladies welcome back",
            "settings": {"aspect": "9:16", "layout": "auto", "subject_x": None, "subject_y": None, "zoom": 1.0,
                         "captions": {"enabled": True, "preset": "opus"}, "hook": {"enabled": True, "seconds": 4.0},
                         "audio": {"normalize": True, "denoise": False}, "remove_silence": False}}
    base.update(overrides)
    return base


def change(changes: dict, current: dict | None = None) -> prompt_edit.Outcome:
    return prompt_edit.make_changes(current or state(), changes, duration=600.0, words=WORDS, fonts=FONTS)


# ------------------------------------------------------------------------------------------------- the cut
def test_the_cut_moves_to_the_new_times_and_never_into_the_middle_of_a_word():
    outcome = change({"start": 10.7, "end": 20.8})            # inside "ladies" and inside "one"

    assert (outcome.state["start"], outcome.state["end"]) == (10.5, 21.0)
    assert any("Starts at" in line for line in outcome.done) and any("Ends at" in line for line in outcome.done)


def test_times_can_be_given_as_a_clock():
    assert change({"start": "0:12.5"}).state["start"] == 12.5


def test_a_cut_shorter_than_a_second_is_not_made_and_the_end_stays_inside_the_video():
    outcome = change({"start": 30.0, "end": 30.5})

    assert outcome.done == [] and outcome.state == state()
    assert "at least 1 second" in outcome.not_done[0]
    assert change({"end": 9999}).state["end"] == 600.0


def test_parts_are_left_out_inside_the_cut_merged_and_never_into_a_word():
    outcome = change({"leave_out": [[15, 18], [17, 19], [20.8, 25], [50, 60]]})

    assert outcome.state["settings"]["cuts"] == [[15.0, 19.0], [20.6, 25.0]]   # 20.8 is inside "one"
    assert any("Leaves out" in line for line in outcome.done)
    assert any("outside the clip" in line for line in outcome.not_done)             # 50-60: the clip ends at 40


def test_a_part_that_ends_just_before_a_word_ends_takes_the_whole_word():
    # seen for real: "know." ran from 9.12 to 9.32 s and the part left out ended at 9.30, leaving 20 ms of it
    outcome = change({"leave_out": [[30.0, 38.78]]})          # "goodbye" runs from 38.0 to 38.8

    assert outcome.state["settings"]["cuts"] == [[30.0, 38.8]]


def test_a_cut_inside_a_word_goes_to_its_nearer_edge():
    assert change({"start": 10.95}).state["start"] == 11.0    # the end of "ladies": it starts after the word
    assert change({"end": 38.1}).state["end"] == 38.0         # the start of "goodbye": it ends before the word


def test_parts_left_out_can_all_be_put_back():
    current = state()
    current["settings"]["cuts"] = [[15.0, 19.0]]

    outcome = change({"leave_out": []}, current)

    assert "cuts" not in outcome.state["settings"]
    assert any("back" in line for line in outcome.done)


def test_leaving_out_almost_everything_is_refused():
    outcome = change({"leave_out": [[10.0, 39.5]]})

    assert outcome.done == [] and "less than 1 second" in outcome.not_done[0]


def test_a_new_cut_resets_subtitles_you_edited_by_hand_and_says_so():
    current = state(captions=[{"text": "Hi", "start": 0.1, "end": 0.5}])

    outcome = change({"end": 30.0}, current)

    assert outcome.state["captions"] is None
    assert any("subtitles you edited" in line for line in outcome.done)


# -------------------------------------------------------------------------------------------- the subtitles
def test_subtitle_changes_are_checked_and_kept_within_what_clipforge_allows():
    outcome = change({"subtitles": {"size": 400, "color": "yellow", "font": "Comic Sans", "words_per_line": 1,
                                    "uppercase": False, "highlight": "#ff0000"}})

    captions = outcome.state["settings"]["captions"]
    assert captions["size"] == 220 and captions["max_words"] == 1 and captions["uppercase"] is False
    assert captions["highlight"] == "#FF0000"
    assert "color" not in captions and "font" not in captions
    assert any("Comic Sans" in line for line in outcome.not_done)
    assert any("yellow" in line for line in outcome.not_done)


def test_a_font_is_found_whatever_its_capitals():
    assert change({"subtitles": {"font": "impact"}}).state["settings"]["captions"]["font"] == "Impact"


def test_choosing_a_subtitle_style_by_its_name_starts_from_that_style():
    current = state()
    current["settings"]["captions"] = {"enabled": True, "preset": "opus", "font": "Arial", "size": 150,
                                       "censor": False}

    outcome = change({"subtitles": {"style": "Gaming"}}, current)

    assert outcome.state["settings"]["captions"] == {"enabled": True, "preset": "gaming", "censor": False}


def test_subtitles_can_be_switched_off():
    outcome = change({"subtitles": {"enabled": False}})

    assert outcome.state["settings"]["captions"]["enabled"] is False
    assert outcome.done == ["Subtitles off"]


def test_word_corrections_are_passed_on_to_be_made_once_the_cut_is_saved():
    outcome = change({"fix_words": [{"from": "jinxy", "to": "Jynxzi"}, {"from": "", "to": "x"}, "junk",
                                    {"from": "two words", "to": "one"}]})

    assert outcome.fixes == [("jinxy", "Jynxzi")]
    assert len(outcome.not_done) == 3


def test_words_are_corrected_in_the_subtitles_however_they_were_written():
    words = [CaptionWord(text="jinxy,", start=0.0, end=0.4), CaptionWord(text="JINXY", start=0.5, end=0.9),
             CaptionWord(text="hello", start=1.0, end=1.4)]

    fixed, counts = prompt_edit.fix_words(words, [("Jinxy", "Jynxzi"), ("nobody", "Somebody")])

    assert [w.text for w in fixed] == ["Jynxzi,", "Jynxzi", "hello"]
    assert counts == {"Jinxy": 2, "nobody": 0}


# ----------------------------------------------------------------------- hook, layout, sound, title, thumbnail
def test_the_hook_title_can_be_rewritten_timed_and_switched_off():
    outcome = change({"hook": {"text": "  She left mid-date  ", "seconds": 40}})
    assert outcome.state["hook_text"] == "She left mid-date"
    assert outcome.state["settings"]["hook"]["seconds"] == 15

    off = change({"hook": {"text": ""}})
    assert off.state["settings"]["hook"]["enabled"] is False
    assert off.state["hook_text"] == "He did not expect that"           # kept, for when it is switched on again


def test_layout_sound_pauses_title_and_thumbnail_words():
    outcome = change({"layout": {"shape": "1:1", "layout": "Facecam over gameplay", "zoom": 9},
                      "sound": {"reduce_noise": True}, "tighten_pauses": True, "title": "She walked out",
                      "thumbnail": {"text": "she walked out", "word": "walked", "moment": 25}})

    st = outcome.state["settings"]
    assert (st["aspect"], st["layout"], st["zoom"]) == ("1:1", "facecam_top", 2.5)
    assert st["audio"]["denoise"] is True and st["remove_silence"] is True
    assert outcome.state["title"] == "She walked out"
    assert (outcome.state["cover_text"], outcome.state["cover_word"]) == ("SHE WALKED OUT", "WALKED")
    assert outcome.state["cover_time"] == 25.0
    assert len(outcome.done) == 8


def test_a_subject_position_crops_to_the_subject_as_the_editor_does():
    st = change({"layout": {"subject_x": 0.3}}).state["settings"]

    assert (st["subject_x"], st["layout"]) == (0.3, "fill")


def framed(changes: dict) -> prompt_edit.Outcome:
    """With the picture: one frame of a 1920x1080 source was sent with the request."""
    return prompt_edit.make_changes(state(), changes, duration=600.0, words=WORDS, fonts=FONTS, frame=(1920, 1080))


def test_two_parts_of_the_picture_go_on_top_and_below_in_a_split_screen():
    # seen for real: "the female camera on top and Jynxzi's camera under it" in a recorded Discord call
    outcome = framed({"layout": {"layout": "split", "top": [58.3, 29.6, 41.1, 40.9],
                                 "bottom": {"x": 16.8, "y": 29.6, "w": 41.0, "h": 40.9}}})

    st = outcome.state["settings"]
    assert st["layout"] == "split"
    assert st["facecam_box"] == [1119, 320, 789, 442] and st["gameplay_box"] == [323, 320, 787, 442]
    assert any(line.startswith("Top part") for line in outcome.done)
    assert any(line.startswith("Bottom part") for line in outcome.done)


def test_a_part_off_the_picture_is_kept_inside_it():
    outcome = framed({"layout": {"layout": "facecam_top", "top": [90, 10, 30, 30], "bottom": [0, 0, 100, 100]}})

    assert outcome.state["settings"]["facecam_box"] == [1728, 108, 192, 324]      # cut at the right edge
    assert outcome.state["settings"]["layout"] == "facecam_top"


def test_a_split_screen_needs_both_parts_and_a_tiny_one_is_refused():
    outcome = framed({"layout": {"layout": "split", "top": [58, 30, 41, 41], "bottom": [1, 1, 2, 2]}})

    assert outcome.state == state() and outcome.done == []                        # nothing half done
    assert any("too small" in line for line in outcome.not_done)


def test_parts_are_only_kept_for_the_stacked_layouts():
    outcome = framed({"layout": {"layout": "fill", "top": [58, 30, 41, 41], "bottom": [17, 30, 41, 41]}})

    st = outcome.state["settings"]
    assert st["layout"] == "fill" and "facecam_box" not in st and "gameplay_box" not in st
    assert any("stacked" in line for line in outcome.not_done)


def test_parts_of_the_picture_need_the_picture():
    outcome = change({"layout": {"layout": "split", "top": [58, 30, 41, 41], "bottom": [17, 30, 41, 41]}})

    assert outcome.state == state() and any("picture" in line for line in outcome.not_done)


def test_the_picture_sent_is_the_one_most_like_the_rest_of_the_clip():
    # seen for real: Discord showed both cameras most of the clip, and only one at its start and end
    import numpy as np

    both = np.zeros((36, 64)); both[10:26, 8:30] = 200; both[10:26, 34:56] = 180
    one = np.zeros((36, 64)); one[4:34, 12:52] = 190

    assert prompt_edit.typical_frame([one, both, both + 3, both - 2, one]) in (1, 2, 3)


def test_frames_across_the_clip_are_shown_side_by_side_with_their_times():
    import io

    from PIL import Image

    frames = [Image.new("RGB", (320, 180), (40 * i, 0, 0)) for i in range(5)]

    sheet = Image.open(io.BytesIO(prompt_edit.frame_sheet(frames, ["0:00", "0:10", "0:20", "0:30", "0:40"])))

    assert sheet.format == "JPEG" and sheet.size == (4 * 320, 2 * 180)


def tile_frames(count: int = 8):  # noqa: ANN201
    """Frames of a recorded call at 640x360: black, with one camera tile at x 100-400, y 80-250 whose picture
    keeps changing."""
    import numpy as np

    rng = np.random.default_rng(7)
    frames = []
    for _ in range(count):  # a camera picture: smooth light that changes from frame to frame, a little noise
        frame = np.zeros((360, 640), dtype=np.float32)
        across = np.linspace(0, rng.uniform(-60, 60), 300)[None, :]
        frame[80:250, 100:400] = rng.uniform(90, 170) + across + rng.normal(0, 3, size=(170, 300))
        frames.append(np.clip(frame, 0, 255).astype(np.uint8))
    return frames


def test_a_part_drawn_round_a_camera_is_moved_onto_the_cameras_own_edges():
    # seen for real: Claude's rectangle began about 15 px above the camera, so a black line showed in the clip
    tile = [300, 240, 900, 510]                                  # the same tile at 1920x1080, three times larger
    drawn = [300 + 24, 240 - 27, 900 - 30, 510 + 20]

    snapped = prompt_edit.snap_part(tile_frames(), drawn, (1920, 1080))

    assert all(abs(a - b) <= 3 for a, b in zip(snapped, tile)), snapped


def test_a_camera_that_moves_during_the_clip_is_cropped_inside_it_in_every_frame():
    # seen for real: Discord's tiles sat 18 px lower while a bar showed at its top
    frames = tile_frames()
    for frame in frames[:4]:                                      # lower by 6 px (18 at 1080) in half the frames
        frame[86:256, 100:400] = frame[80:250, 100:400].copy()
        frame[80:86, 100:400] = 0

    snapped = prompt_edit.snap_part(frames, [300, 225, 900, 540], (1920, 1080))

    assert abs(snapped[1] - 258) <= 3 and abs(snapped[1] + snapped[3] - 750) <= 3, snapped   # rows 86-250 at 1/3


def test_a_line_inside_the_picture_is_not_taken_for_the_cameras_edge():
    # seen for real: the white door frame in Jynxzi's room pulled the crop 95 px inwards
    frames = tile_frames()
    for frame in frames:
        frame[80:250, 370:374] = 250                              # a bright, fixed line close to the right edge

    snapped = prompt_edit.snap_part(frames, [300, 240, 900 + 20, 510], (1920, 1080))

    assert abs(snapped[0] + snapped[2] - 1200) <= 3, snapped     # the tile's own edge, not the line


def test_a_side_drawn_at_the_edge_of_the_picture_stays_there():
    # a fixed line close to the top of the picture must not pull a part drawn from the very top down onto it
    frames = tile_frames()
    for frame in frames:
        frame[10:12, :] = 255

    assert prompt_edit.snap_part(frames, [300, 0, 900, 750], (1920, 1080))[:2] == [300, 0]


def test_a_part_with_no_clear_edges_near_it_stays_as_drawn():
    import numpy as np

    rng = np.random.default_rng(3)
    noise = [rng.integers(0, 255, size=(360, 640)).astype(np.uint8) for _ in range(8)]

    assert prompt_edit.snap_part(noise, [300, 240, 900, 510], (1920, 1080)) == [300, 240, 900, 510]


def test_parts_are_snapped_before_they_are_kept():
    outcome = prompt_edit.make_changes(state(), {"layout": {"layout": "split", "top": [58, 30, 41, 41],
                                                            "bottom": [17, 30, 41, 41]}},
                                       duration=600.0, words=WORDS, fonts=FONTS, frame=(1920, 1080),
                                       snap=lambda box: [v + 1 for v in box])

    assert outcome.state["settings"]["facecam_box"] == [1115, 325, 788, 444]           # [1114, 324, 787, 443] + 1


def test_what_clipforge_cannot_do_is_reported_not_faked():
    outcome = change({"speed": 1.5, "music": "lofi", "subtitles": {"wobble": True}})

    assert outcome.done == [] and outcome.state == state()
    assert len(outcome.not_done) == 3 and "speed" in outcome.not_done[0]


def test_asking_for_what_the_clip_already_has_changes_nothing():
    outcome = change({"start": 10.0, "subtitles": {"enabled": True, "size": 92, "style": "opus"},
                      "hook": {"text": "He did not expect that", "seconds": 4}, "layout": {"layout": "auto"}})

    assert outcome.done == [] and outcome.state == state()


def test_the_clip_given_is_never_changed_in_place():
    current = state()
    kept = copy.deepcopy(current)

    change({"start": 12.0, "subtitles": {"size": 120}, "leave_out": [[15, 18]]}, current)

    assert current == kept


# ------------------------------------------------------------------------------------- Claude's side of it
def test_the_request_shows_claude_the_clip_its_words_and_what_can_be_changed():
    sentences = [(5.0, 8.0, "Before the clip"), (10.0, 12.3, "Okay ladies welcome back"), (45.0, 47.0, "After it")]

    text = prompt_edit.build_prompt("Start when he says okay ladies", state(), video_title="20 women vs Jynxzi",
                                    creator="Jynxzi", duration=600.0, sentences=sentences,
                                    segments=[(10.0, 40.0)], fonts=FONTS)

    assert "Start when he says okay ladies" in text and "20 women vs Jynxzi" in text
    assert "> [source 10.0-12.3 s | clip 0:00.0-0:02.3] Okay ladies welcome back" in text
    assert "  [source 5.0-8.0 s] Before the clip" in text
    assert "Impact" in text and "not_possible" in text and "size 92" in text


def test_a_long_transcript_is_cut_down_from_the_far_ends_first():
    sentences = [(float(t), t + 0.9, f"line {t} " + "word " * 30) for t in range(0, 600)]

    text = prompt_edit.build_prompt("Shorter", state(), video_title="v", creator="", duration=600.0,
                                    sentences=sentences, segments=[(10.0, 40.0)], fonts=FONTS)

    assert len(text) < 30000
    assert "line 10 " in text and "line 39 " in text                  # the whole clip
    assert "line 9 " in text and "line 40 " in text                   # and what is right around it


def test_claudes_answer_is_read_from_its_json():
    printed = json.dumps({"type": "result", "is_error": False, "result": 'Sure!\n```json\n{"changes": {"start": 12}, '
                          '"not_possible": ["No music"], "summary": "Starts earlier."}\n```'})

    answer = prompt_edit.parse_answer(printed)

    assert answer.changes == {"start": 12} and answer.not_possible == ["No music"]
    assert answer.summary == "Starts earlier."


def test_an_answer_that_cannot_be_read_is_an_error_not_a_guess():
    with pytest.raises(ClipForgeError):
        prompt_edit.parse_answer(json.dumps({"type": "result", "is_error": False, "result": "I made it bigger"}))
    with pytest.raises(ClipForgeError):
        prompt_edit.parse_answer(json.dumps({"type": "result", "is_error": True, "result": "Not signed in"}))


def test_parts_left_out_are_taken_out_of_the_clips_timeline():
    assert pipeline.clip_segments(0.0, 10.0, [], False, cuts=[[2, 3], [5, 6]]) == [(0.0, 2.0), (3.0, 5.0),
                                                                                   (6.0, 10.0)]
    assert pipeline.clip_segments(0.0, 10.0, [], False, cuts=[[-1, 2], [9.5, 12]]) == [(2.0, 9.5)]


# ------------------------------------------------------------------------------------------------- the app
def claude_answers(monkeypatch, answer: dict, asked: list[str] | None = None) -> None:  # noqa: ANN001
    def fake(prompt, **kw):  # noqa: ANN001, ANN003, ANN202
        if asked is not None:
            asked.append(prompt)
        return json.dumps({"type": "result", "is_error": False, "result": json.dumps(answer)})

    monkeypatch.setattr(claude_review, "run_claude", fake)


@pytest.fixture()
def claude(client: TestClient) -> None:  # noqa: F811
    assert client.put("/api/settings", json={"values": {"ai.engine": "claude"}}).status_code == 200


def edit(client: TestClient, clip: dict, instruction: str):  # noqa: F811, ANN201
    return client.post(f"/api/clips/{clip['id']}/prompt-edit", json={"instruction": instruction})


def renders_of(clip: dict) -> int:  # noqa: F811
    with db.session_scope() as s:
        return s.query(Job).filter(Job.clip_id == clip["id"], Job.type == "render").count()


def test_a_prompt_edit_changes_the_clip_and_renders_it_again(client: TestClient, clip: dict, claude: None,  # noqa: F811
                                                             monkeypatch):  # noqa: ANN001
    asked: list[str] = []
    claude_answers(monkeypatch, {"changes": {"subtitles": {"size": 130}, "hook": {"text": "New hook"}},
                                 "not_possible": [], "summary": "Bigger subtitles and a new hook."}, asked)

    response = edit(client, clip, "Bigger subtitles, and the hook should say New hook")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rendering"] is True and body["summary"] == "Bigger subtitles and a new hook."
    assert any("130" in line for line in body["done"]) and body["not_done"] == []
    edited = body["clip"]
    assert edited["settings"]["captions"]["size"] == 130 and edited["hook_text"] == "New hook"
    assert edited["render_status"] == "queued" and renders_of(clip) == 1
    assert edited["last_edit"]["instruction"] == "Bigger subtitles, and the hook should say New hook"
    assert "before" not in edited["last_edit"]                         # the copy for undo stays on this PC's side
    assert "Bigger subtitles, and the hook should say New hook" in asked[0] and "My pick" in asked[0]


def test_a_picture_of_the_video_goes_with_the_request_so_parts_of_it_can_be_chosen(client: TestClient,  # noqa: F811
                                                                                 clip: dict, claude: None,  # noqa: F811
                                                                                 monkeypatch):  # noqa: ANN001
    seen: dict = {}

    def fake(prompt, **kw):  # noqa: ANN001, ANN003, ANN202
        seen["pictures"], seen["prompt"] = kw.get("pictures"), prompt
        return json.dumps({"type": "result", "is_error": False, "result": json.dumps(
            {"changes": {"layout": {"layout": "split", "top": [50, 0, 50, 100], "bottom": [0, 0, 50, 100]}},
             "summary": "Right camera on top."})})

    monkeypatch.setattr(claude_review, "run_claude", fake)

    body = edit(client, clip, "The right camera on top, the left one below").json()

    assert len(seen["pictures"]) == 2 and all(p[:2] == bytes([0xFF, 0xD8]) for p in seen["pictures"])  # JPEGs
    assert "Two pictures are attached" in seen["prompt"] and "640x360" in seen["prompt"]
    st = body["clip"]["settings"]
    assert st["layout"] == "split"
    assert st["facecam_box"] == [320, 0, 320, 360] and st["gameplay_box"] == [0, 0, 320, 360]


def test_undo_puts_the_clip_back_as_it_was_and_renders_it_again(client: TestClient, clip: dict,  # noqa: F811
                                                                claude: None, monkeypatch):  # noqa: ANN001
    claude_answers(monkeypatch, {"changes": {"start": 1.0, "subtitles": {"enabled": False}, "title": "Changed",
                                             "leave_out": [[1.5, 1.8]]}, "summary": "Shorter, no subtitles."})
    assert edit(client, clip, "Start later, no subtitles, new title").status_code == 200
    with db.session_scope() as s:                                      # the render it asked for is done
        s.get(Clip, clip["id"]).render_status = "ready"
        for job in s.query(Job).filter(Job.clip_id == clip["id"]):
            job.status = "succeeded"

    response = client.post(f"/api/clips/{clip['id']}/prompt-edit/undo")

    assert response.status_code == 200, response.text
    back = response.json()
    assert (back["start"], back["end"], back["title"]) == (clip["start"], clip["end"], "My pick")
    assert back["settings"] == clip["settings"] and back["last_edit"] is None
    assert back["render_status"] == "queued" and renders_of(clip) == 2


def test_there_is_nothing_to_undo_before_a_prompt_edit(client: TestClient, clip: dict):  # noqa: F811
    response = client.post(f"/api/clips/{clip['id']}/prompt-edit/undo")

    assert response.status_code == 404 and "nothing to undo" in response.json()["error"].lower()


def test_when_claude_finds_nothing_it_can_do_nothing_is_rendered_and_it_says_why(client: TestClient,  # noqa: F811
                                                                               clip: dict, claude: None,  # noqa: F811
                                                                               monkeypatch):  # noqa: ANN001
    claude_answers(monkeypatch, {"changes": {}, "not_possible": ["ClipForge cannot add music yet."],
                                 "summary": "Nothing changed."})

    body = edit(client, clip, "Add lofi music").json()

    assert body["rendering"] is False and body["done"] == []
    assert body["not_done"] == ["ClipForge cannot add music yet."]
    assert body["clip"]["render_status"] == "none" and body["clip"]["last_edit"] is None and renders_of(clip) == 0


def test_a_word_fix_is_made_in_the_subtitles(client: TestClient, clip: dict, claude: None,  # noqa: F811
                                             monkeypatch):  # noqa: ANN001
    with db.session_scope() as s:
        s.get(Clip, clip["id"]).captions = [{"text": "jinxy", "start": 0.1, "end": 0.5},
                                            {"text": "wins", "start": 0.6, "end": 1.0}]
    claude_answers(monkeypatch, {"changes": {"fix_words": [{"from": "Jinxy", "to": "Jynxzi"},
                                                           {"from": "loses", "to": "wins"}]},
                                 "summary": "Fixed the name."})

    body = edit(client, clip, "His name is spelled Jynxzi").json()

    words = client.get(f"/api/clips/{clip['id']}/captions").json()["words"]
    assert [w["text"] for w in words] == ["Jynxzi", "wins"]
    assert any("Jynxzi" in line for line in body["done"]) and any("loses" in line for line in body["not_done"])


def test_a_clip_that_is_being_rendered_is_left_alone(client: TestClient, clip: dict, claude: None,  # noqa: F811
                                                     monkeypatch):  # noqa: ANN001
    asked: list[str] = []
    claude_answers(monkeypatch, {"changes": {"title": "x"}, "summary": "x"}, asked)
    assert client.post(f"/api/clips/{clip['id']}/render").status_code == 200

    response = edit(client, clip, "Bigger subtitles")

    assert response.status_code == 409 and asked == []
    assert client.post(f"/api/clips/{clip['id']}/prompt-edit/undo").status_code == 409


def test_with_local_rules_only_nothing_is_sent_to_claude(client: TestClient, clip: dict,  # noqa: F811
                                                         monkeypatch):  # noqa: ANN001
    asked: list[str] = []
    claude_answers(monkeypatch, {"changes": {"title": "x"}, "summary": "x"}, asked)

    response = edit(client, clip, "Bigger subtitles")

    assert response.status_code == 400 and "switched off" in response.json()["error"] and asked == []


def test_an_empty_or_very_long_request_is_refused(client: TestClient, clip: dict, claude: None):  # noqa: F811
    assert edit(client, clip, "   ").status_code in (400, 422)
    assert edit(client, clip, "x" * 1001).status_code == 422


def test_when_claude_is_not_signed_in_the_clip_is_left_as_it_was(client: TestClient, clip: dict,  # noqa: F811
                                                                 claude: None, monkeypatch):  # noqa: ANN001
    def signed_out(prompt, **kw):  # noqa: ANN001, ANN003, ANN202
        raise claude_review.ClaudeUnavailable("Claude Code is signed out, so nothing was changed.", hint="Sign in.")

    monkeypatch.setattr(claude_review, "run_claude", signed_out)

    response = edit(client, clip, "Bigger subtitles")

    assert response.status_code == 503 and "signed out" in response.json()["error"]
    assert client.get(f"/api/clips/{clip['id']}").json()["settings"] == clip["settings"]


def test_trimming_a_clip_keeps_its_parts_left_out_inside_it(client: TestClient, clip: dict):  # noqa: F811
    # ChatGPT's review: a trim in the editor left parts outside the clip saved, to come back if it grew again
    cid = clip["id"]
    assert client.patch(f"/api/clips/{cid}", json={"settings": {"cuts": [[1.0, 1.5], [2.0, 2.2]]}}).status_code == 200

    shorter = client.patch(f"/api/clips/{cid}", json={"end": 2.0}).json()
    assert shorter["settings"]["cuts"] == [[1.0, 1.5]]                 # 2.0-2.2 is past the new end
    later = client.patch(f"/api/clips/{cid}", json={"start": 1.2}).json()
    assert later["settings"]["cuts"] == [[1.2, 1.5]]                    # trimmed to the new start
    inside = client.patch(f"/api/clips/{cid}", json={"start": 1.6}).json()
    assert "cuts" not in inside["settings"]                             # nothing of it is left in the clip


def test_a_trim_that_would_leave_only_parts_left_out_is_refused(client: TestClient, clip: dict):  # noqa: F811
    cid = clip["id"]
    assert client.patch(f"/api/clips/{cid}", json={"settings": {"cuts": [[1.0, 1.5]]}}).status_code == 200

    response = client.patch(f"/api/clips/{cid}", json={"start": 1.0, "end": 1.5})

    assert response.status_code == 400 and "left out" in response.json()["error"]
    assert client.get(f"/api/clips/{cid}").json()["start"] == clip["start"]          # nothing was changed
    covering = client.patch(f"/api/clips/{cid}", json={"settings": {"cuts": [[0.0, 3.0]]}})
    assert covering.status_code == 400


def test_parts_left_out_are_stored_with_the_clip_and_checked(client: TestClient, clip: dict):  # noqa: F811
    ok = client.patch(f"/api/clips/{clip['id']}", json={"settings": {"cuts": [[1.0, 1.5]]}})

    assert ok.status_code == 200 and ok.json()["settings"]["cuts"] == [[1.0, 1.5]]
    assert client.patch(f"/api/clips/{clip['id']}", json={"settings": {"cuts": [[1.5, 1.0]]}}).status_code == 400
    assert client.patch(f"/api/clips/{clip['id']}", json={"settings": {"cuts": "everything"}}).status_code == 400
