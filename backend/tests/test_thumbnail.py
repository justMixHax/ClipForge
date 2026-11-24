"""Thumbnails: the best moment of a clip, framed like the clip, with a few big words on it that make people want
to watch."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.analysis.describe import clean_words, cover_words
from app.editing import thumbnail
from app.editing.thumbnail import compose_frame, draw_cover, layout_text, make_cover, pick_moment
from app.services import storage

W, H = 1080, 1920
FILL = {"layout": "fill", "out_w": W, "out_h": H, "crop": [218, 0, 202, 360], "fg": None, "top": None,
        "bottom": None, "caption_y": 0.7, "hook_y": 0.16}
STACKED = {"layout": "facecam_top", "out_w": W, "out_h": H, "crop": None, "fg": None,
           "top": {"crop": [0, 0, 640, 450], "height": 760}, "bottom": {"crop": [0, 0, 640, 1138], "height": 1160},
           "caption_y": 0.7, "hook_y": 0.4}


# ------------------------------------------------------------------------------------------------ the words
@pytest.mark.parametrize("line, words, coloured", [
    ("He lost his phone in his own hand", "HE LOST HIS PHONE", "PHONE"),
    ("Okay so, 500 acres of Texas for $250!", "500 ACRES OF TEXAS FOR $250", "$250"),     # a price is the hook
    ("Scam Call Gets Presidential", "SCAM CALL GETS PRESIDENTIAL", "PRESIDENTIAL"),
    ("I KNOW HOW TO OPEN DOORS!", "I KNOW HOW TO OPEN DOORS", "DOORS"),             # a short line is kept whole
    ("Oh my God, oh my God, oh my God, oh my God, oh my God", "OH MY GOD", "GOD"),   # said once is enough
    ("Wow the herb tree okay that's actually beautiful", "WOW THE HERB TREE", "TREE"),  # not ending on "okay"
    ("Season 2 is trash bro everybody says that", "SEASON 2 IS TRASH", "TRASH"),      # one digit is not the hook
    ("Привет мир это тест", "ПРИВЕТ МИР ЭТО ТЕСТ", "ТЕСТ"),                          # every script keeps its words
    ("مرحبا بالعالم", "مرحبا بالعالم", "بالعالم"),
    ("", "WATCH THIS", "WATCH"),
])
def test_the_words_come_from_the_clips_own_line_and_end_on_a_real_word(line: str, words: str, coloured: str):
    assert cover_words(line) == (words, coloured)


def test_words_you_type_become_one_short_line_with_a_coloured_word_that_is_really_in_it():
    assert clean_words("he said\nWHAT??", "what??") == ("HE SAID WHAT??", "WHAT")
    assert clean_words("he said what", "banana") == ("HE SAID WHAT", "WHAT")      # not in it: the last real word
    assert len(clean_words("word " * 30)[0]) <= 40
    assert clean_words("   ") == ("", "")
    assert clean_words("wow 😂 he did it 🔥🔥", "did") == ("WOW HE DID IT", "DID")       # the font has no emoji
    assert clean_words("one two three four five six seven eighteen") == (
        "ONE TWO THREE FOUR FIVE SIX SEVEN", "SEVEN")                                  # cut at a word


# ----------------------------------------------------------------------------------------------- the layout
def test_on_a_full_frame_the_words_sit_high_in_the_safe_area():
    layout = layout_text("SCAM CALL GETS PRESIDENTIAL", (W, H), FILL)

    x0, y0, x1, y1 = layout.box
    assert x0 >= 0.06 * W and x1 <= 0.94 * W                       # clear of the edges
    assert y0 >= 0.06 * H and y1 <= 0.72 * H                       # clear of TikTok's caption and buttons below
    assert 0.18 * H <= (y0 + y1) / 2 <= 0.45 * H
    assert layout.size >= 0.055 * H and len(layout.lines) <= 3


def test_on_the_stacked_layout_the_words_sit_under_the_camera_so_the_face_stays_visible():
    layout = layout_text("HE LOST HIS PHONE", (W, H), STACKED)

    x0, y0, x1, y1 = layout.box
    assert 760 <= y0 <= 760 + 0.05 * H and y1 <= 0.72 * H


def test_on_a_full_frame_the_words_move_out_of_the_way_of_a_face():
    face = (300, 380, 480, 520)                                    # x0, y0, x1, y1 in the picture: the upper part

    layout = layout_text("SCAM CALL GETS PRESIDENTIAL", (W, H), FILL, avoid=[face])

    x0, y0, x1, y1 = layout.box
    assert y0 >= face[3] and y1 <= 0.72 * H


def test_long_words_wrap_onto_at_most_three_lines_and_stay_inside():
    layout = layout_text("THE MOST UNBELIEVABLE INTERNATIONAL SCAMMER CONVERSATION", (W, H), FILL)

    x0, y0, x1, y1 = layout.box
    assert len(layout.lines) <= 3 and x0 >= 0.06 * W and x1 <= 0.94 * W and y1 <= 0.72 * H


def test_one_long_word_is_made_smaller_until_it_fits_inside():
    layout = layout_text("W" * 40, (W, H), FILL)

    font = thumbnail._font(layout.font_path, layout.size)
    widest = max(ImageDraw.Draw(Image.new("RGB", (8, 8))).textlength(line, font=font) for line in layout.lines)
    assert widest + 2 * layout.stroke <= (0.94 - 0.06) * W


def test_words_the_fonts_here_cannot_draw_are_left_off_rather_than_shown_as_boxes():
    assert thumbnail.can_draw("SCAM CALL") and thumbnail.can_draw("ПРИВЕТ МИР")
    assert not thumbnail.can_draw("مرحبا بالعالم")                    # needs text shaping, not set up here

    image = draw_cover(Image.new("RGB", (W, H), (40, 60, 90)), "مرحبا بالعالم", "بالعالم", FILL)

    pixels = np.asarray(image.convert("RGB")).reshape(-1, 3)
    assert image.size == (W, H) and not (pixels > 235).all(axis=1).any()


def test_the_words_are_drawn_white_with_one_word_in_colour():
    image = draw_cover(Image.new("RGB", (W, H), (40, 60, 90)), "SCAM CALL GETS PRESIDENTIAL", "PRESIDENTIAL", FILL)

    pixels = np.asarray(image.convert("RGB")).reshape(-1, 3).astype(int)
    white = ((pixels > 235).all(axis=1)).sum()
    yellow = ((pixels[:, 0] > 230) & (pixels[:, 1] > 190) & (pixels[:, 2] < 60)).sum()
    assert image.size == (W, H) and white > 5000 and yellow > 5000


# ------------------------------------------------------------------------------------- the moment and frame
def joined_video(path: Path, parts: list[tuple[str, float]]) -> Path:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for source, seconds in parts:
        joiner = ":" if "=" in source else "="
        cmd += ["-f", "lavfi", "-t", str(seconds), "-i", f"{source}{joiner}s=640x360:r=30"]
    labels = "".join(f"[{i}:v]" for i in range(len(parts)))
    cmd += ["-filter_complex", f"{labels}concat=n={len(parts)}:v=1:a=0[v]", "-map", "[v]",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


@pytest.mark.ffmpeg
def test_the_moment_chosen_is_sharp_and_lit_not_dark_or_flat(tmp_path: Path):
    video = joined_video(tmp_path / "v.mp4", [("color=c=0x101010", 4), ("testsrc2", 4), ("color=c=0x808080", 4)])

    moment = pick_moment(video, 0.0, 12.0, FILL, (640, 360))

    assert 4.0 <= moment <= 8.0



@pytest.mark.ffmpeg
def test_the_moment_is_never_taken_from_a_part_left_out(tmp_path: Path):
    video = joined_video(tmp_path / "v.mp4", [("color=c=0x101010", 4), ("testsrc2", 4), ("color=c=0x808080", 4)])

    moment = pick_moment(video, 0.0, 12.0, FILL, (640, 360), inside=[(0.0, 4.0), (8.0, 12.0)])

    assert not 4.0 < moment < 8.0

@pytest.mark.ffmpeg
def test_the_frame_is_framed_like_the_clip(tmp_path: Path):
    video = joined_video(tmp_path / "v.mp4", [("testsrc2", 3)])

    frame = compose_frame(video, 1.0, FILL, tmp_path / "frame.png")

    assert Image.open(frame).size == (W, H)


@pytest.mark.ffmpeg
def test_a_cover_is_made_from_the_clip_at_its_best_moment(tmp_path: Path):
    video = joined_video(tmp_path / "v.mp4", [("color=c=0x101010", 2), ("testsrc2", 3)])

    path, moment = make_cover(video, 0.0, 5.0, FILL, (640, 360), "SCAM CALL", "CALL", tmp_path)

    assert path == tmp_path / "thumbnail.jpg" and path.is_file()
    assert Image.open(path).size == (W, H) and Image.open(path).format == "JPEG"
    assert 2.0 <= moment <= 5.0


@pytest.mark.ffmpeg
def test_a_moment_you_choose_is_used_as_given(tmp_path: Path):
    video = joined_video(tmp_path / "v.mp4", [("color=c=0x101010", 2), ("testsrc2", 3)])

    _, moment = make_cover(video, 0.0, 5.0, FILL, (640, 360), "SCAM CALL", "CALL", tmp_path, at=0.5)

    assert moment == 0.5


def test_without_a_face_detector_the_choice_still_works(monkeypatch):  # noqa: ANN001
    monkeypatch.setattr(thumbnail, "detector_available", lambda: False)
    sharp = np.tile(np.array([0, 255], dtype=np.uint8), (90, 80))
    flat = np.full((90, 160), 128, dtype=np.uint8)

    assert thumbnail.frame_score(np.dstack([sharp] * 3), None) > thumbnail.frame_score(np.dstack([flat] * 3), None)


def test_a_picture_that_is_being_sent_to_the_browser_is_replaced_once_it_is_free(tmp_path: Path, monkeypatch):  # noqa: ANN001
    real, calls = os.replace, []

    def busy(src, dst):  # noqa: ANN001, ANN202  (Windows refuses while the old file is open)
        calls.append(src)
        if len(calls) < 3:
            raise PermissionError(13, "The process cannot access the file")
        real(src, dst)

    monkeypatch.setattr(os, "replace", busy)
    (tmp_path / "new.jpg").write_bytes(b"new")

    storage.replace_file(tmp_path / "new.jpg", tmp_path / "thumbnail.jpg")

    assert (tmp_path / "thumbnail.jpg").read_bytes() == b"new" and len(calls) == 3


@pytest.mark.ffmpeg
def test_with_a_camera_that_follows_the_frame_is_taken_where_the_camera_is_then(tmp_path: Path):
    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-t", "4", "-i", "color=c=black:s=320x360:r=30",
                    "-f", "lavfi", "-t", "4", "-i", "color=c=white:s=320x360:r=30",
                    "-filter_complex", "[0:v][1:v]hstack=inputs=2[v]", "-map", "[v]",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(video)],
                   check=True, capture_output=True)
    moving = {**FILL, "crop": [0, 0, 202, 360], "track": [[0.0, 0], [1.0, 0], [3.0, 438]]}

    early = np.asarray(Image.open(compose_frame(video, 0.5, moving, tmp_path / "a.png")).convert("L"))
    late = np.asarray(Image.open(compose_frame(video, 3.5, moving, tmp_path / "b.png")).convert("L"))

    assert early.mean() < 20 and late.mean() > 235


def test_the_part_scored_for_the_best_moment_is_where_the_following_camera_is_then():
    frame = np.zeros((90, 160, 3), dtype=np.uint8)
    frame[:, 80:] = 255                                              # left half dark, right half light
    moving = {"layout": "fill", "crop": [0, 0, 320, 360], "track": [[0.0, 0], [2.0, 320]]}

    early = thumbnail._visible(frame, moving, (640, 360), 0.0)
    late = thumbnail._visible(frame, moving, (640, 360), 2.0)

    assert early.mean() == 0 and late.mean() == 255
    assert thumbnail._visible(frame, {**moving, "track": None}, (640, 360), 2.0).mean() == 0   # no path: as before
