from __future__ import annotations

import numpy as np
import pytest

from app.editing.reframe import (ASPECTS, FaceBox, FaceSummary, build_filter, plan_layout, refine_box_with_edges,
                                 summarize_faces)

W, H = 1920, 1080
NO_FACES = FaceSummary(mode="none", frames=10)


def cam(x: float, y: float = 300, w: float = 420, h: float = 480) -> FaceSummary:
    return FaceSummary(mode="cam", face=(x, y, w, h), frames=10, frames_with_face=10)


# ------------------------------------------------------------------------------------------- fill crop
def test_fill_crop_has_the_target_shape_and_is_centred_by_default():
    plan = plan_layout(W, H, "9:16", "fill", NO_FACES)

    assert plan.layout == "fill"
    assert (plan.out_w, plan.out_h) == (1080, 1920)
    assert plan.crop == (656, 0, 608, 1080)


def test_fill_follows_the_chosen_subject_position_and_stops_at_the_edges():
    assert plan_layout(W, H, "9:16", "fill", NO_FACES, subject_x=0.5).crop[0] == 656
    assert plan_layout(W, H, "9:16", "fill", NO_FACES, subject_x=1.0).crop[0] == W - 608
    assert plan_layout(W, H, "9:16", "fill", NO_FACES, subject_x=0.0).crop[0] == 0


def test_fill_centres_on_the_detected_face():
    plan = plan_layout(W, H, "9:16", "fill", cam(x=1190))      # face centre at x = 1400

    assert plan.crop[0] == pytest.approx(1400 - 304, abs=2)


def test_a_manual_subject_position_overrides_the_detected_face():
    plan = plan_layout(W, H, "9:16", "fill", cam(x=1190), subject_x=0.25)

    assert plan.crop[0] == pytest.approx(0.25 * W - 304, abs=2)


def test_zoom_tightens_the_crop():
    plan = plan_layout(W, H, "9:16", "fill", NO_FACES, zoom=1.5)
    x, y, w, h = plan.crop

    assert (w, h) == (406, 720)
    assert x + w / 2 == pytest.approx(W / 2, abs=2) and y + h / 2 == pytest.approx(H / 2, abs=2)


def test_a_source_already_in_the_target_shape_is_used_whole():
    assert plan_layout(1080, 1920, "9:16", "auto", NO_FACES).crop == (0, 0, 1080, 1920)
    assert plan_layout(W, H, "16:9", "auto", NO_FACES).crop == (0, 0, W, H)


# ------------------------------------------------------------------------------------------- fit + blur
def test_blurred_fill_keeps_the_whole_frame_visible_in_the_middle():
    plan = plan_layout(W, H, "9:16", "fit_blur", NO_FACES)

    assert plan.layout == "fit_blur"
    assert plan.fg == (0, 656, 1080, 608)
    assert (656 + 608) / 1920 < plan.caption_y <= 0.80           # captions sit under the picture
    assert plan.hook_y < 656 / 1920                              # the title sits above it


# -------------------------------------------------------------------------------------- facecam stack
def test_facecam_layout_stacks_the_camera_over_the_gameplay():
    faces = FaceSummary(mode="facecam", face=(180, 150, 120, 130), facecam_box=(40, 60, 420, 300), frames=10,
                        frames_with_face=10)
    plan = plan_layout(W, H, "9:16", "facecam_top", faces)

    assert plan.layout == "facecam_top"
    assert plan.top["height"] + plan.bottom["height"] == 1920
    assert 560 <= plan.top["height"] <= 960
    tx, ty, tw, th = plan.top["crop"]
    assert tx >= 40 - 2 and ty >= 60 - 2 and tx + tw <= 460 + 2 and ty + th <= 360 + 2   # inside the facecam
    assert tw / th == pytest.approx(1080 / plan.top["height"], rel=0.02)
    bx, by, bw, bh = plan.bottom["crop"]
    assert bw / bh == pytest.approx(1080 / plan.bottom["height"], rel=0.02)
    assert plan.top["height"] / 1920 < plan.caption_y <= 0.80


def test_manual_boxes_are_used_as_given():
    plan = plan_layout(W, H, "9:16", "facecam_top", NO_FACES, facecam_box=(1400, 700, 480, 360),
                       gameplay_box=(0, 0, 1400, 1080))

    tx, ty, tw, th = plan.top["crop"]
    assert 1400 <= tx and tx + tw <= 1880 and 700 <= ty and ty + th <= 1060
    bx, by, bw, bh = plan.bottom["crop"]
    assert bx >= 0 and bx + bw <= 1400


def test_asking_for_the_stack_without_a_facecam_falls_back_and_says_why():
    plan = plan_layout(W, H, "9:16", "facecam_top", NO_FACES)

    assert plan.layout == "fit_blur"
    assert "facecam" in plan.reason.lower()


# -------------------------------------------------------------------------------------------- automatic
def test_auto_keeps_the_whole_frame_when_there_is_no_face():
    assert plan_layout(W, H, "9:16", "auto", NO_FACES).layout == "fit_blur"


def test_auto_crops_to_the_face_for_a_talking_head():
    plan = plan_layout(W, H, "9:16", "auto", cam(x=700))
    assert plan.layout == "fill" and plan.reason


def test_auto_stacks_when_a_small_facecam_is_found():
    faces = FaceSummary(mode="facecam", face=(180, 150, 120, 130), facecam_box=(40, 60, 420, 300), frames=10,
                        frames_with_face=9)
    assert plan_layout(W, H, "9:16", "auto", faces).layout == "facecam_top"


def test_auto_uses_one_crop_for_two_people_sitting_close_and_split_screen_when_far_apart():
    close = FaceSummary(mode="two", face=(700, 300, 200, 240), second=(1000, 300, 200, 240), frames=10,
                        frames_with_face=10)
    apart = FaceSummary(mode="two", face=(250, 300, 260, 300), second=(1400, 300, 260, 300), frames=10,
                        frames_with_face=10)

    together = plan_layout(W, H, "9:16", "auto", close)
    split = plan_layout(W, H, "9:16", "auto", apart)

    assert together.layout == "fill"
    assert together.crop[0] <= 700 and together.crop[0] + together.crop[2] >= 1200       # both faces inside
    assert split.layout == "split"
    assert split.top["height"] + split.bottom["height"] == 1920


def test_gaming_preset_prefers_the_whole_frame_over_cropping_to_a_small_face():
    """A face that is neither big nor a clear facecam: keep all the gameplay rather than crop into it."""
    plan = plan_layout(W, H, "9:16", "auto", NO_FACES, preset="gaming")
    assert plan.layout == "fit_blur"


# ---------------------------------------------------------------------------------------------- safety
@pytest.mark.parametrize("src", [(1920, 1080), (1280, 720), (2560, 1440), (3840, 2160), (1080, 1920), (640, 480)])
@pytest.mark.parametrize("aspect", list(ASPECTS))
@pytest.mark.parametrize("layout", ["auto", "fill", "fit_blur", "facecam_top", "split"])
def test_every_number_is_even_and_inside_the_source(src, aspect, layout):
    sw, sh = src
    faces = FaceSummary(mode="two", face=(sw * 0.1, sh * 0.3, sw * 0.12, sh * 0.2),
                        second=(sw * 0.7, sh * 0.3, sw * 0.12, sh * 0.2),
                        facecam_box=(sw * 0.05, sh * 0.2, sw * 0.25, sh * 0.35), frames=10, frames_with_face=10)
    plan = plan_layout(sw, sh, aspect, layout, faces, subject_x=0.93, zoom=1.3)

    boxes = [plan.crop] + [part["crop"] for part in (plan.top, plan.bottom) if part]
    for box in (b for b in boxes if b):
        x, y, w, h = box
        assert all(v % 2 == 0 for v in box), box
        assert x >= 0 and y >= 0 and w > 0 and h > 0 and x + w <= sw and y + h <= sh, box
    assert (plan.out_w, plan.out_h) == ASPECTS[aspect]
    assert 0.10 <= plan.caption_y <= 0.80 and 0.05 <= plan.hook_y <= 0.60
    graph = build_filter(plan)
    assert graph.startswith("[vin]") and graph.endswith("[base]")


def test_filter_graphs_name_the_right_operations():
    assert "crop=608:1080:656:0" in build_filter(plan_layout(W, H, "9:16", "fill", NO_FACES))
    assert "boxblur" in build_filter(plan_layout(W, H, "9:16", "fit_blur", NO_FACES))
    stacked = build_filter(plan_layout(W, H, "9:16", "facecam_top", NO_FACES, facecam_box=(40, 60, 420, 300)))
    assert "vstack" in stacked


# ---------------------------------------------------------------------------------------- face summary
def frames_with(box: tuple[float, float, float, float], count: int, total: int = 10, jitter: float = 4.0):
    rng = np.random.default_rng(3)
    frames = []
    for k in range(total):
        if k < count:
            dx, dy = rng.uniform(-jitter, jitter, 2)
            frames.append([FaceBox(box[0] + dx, box[1] + dy, box[2], box[3], 0.9)])
        else:
            frames.append([])
    return frames


def test_a_small_face_that_stays_in_one_place_is_a_facecam():
    summary = summarize_faces(frames_with((180, 150, 120, 130), 10), W, H)

    assert summary.mode == "facecam"
    x, y, w, h = summary.facecam_box
    assert x <= 180 and y <= 150 and x + w >= 300 and y + h >= 280          # contains the face
    assert w >= 120 * 1.4 and x >= 0 and y >= 0 and x + w <= W and y + h <= H


def test_a_large_face_is_a_camera_shot_not_an_overlay():
    assert summarize_faces(frames_with((700, 250, 480, 560), 10), W, H).mode == "cam"


def test_a_face_seen_only_now_and_then_does_not_count():
    summary = summarize_faces(frames_with((180, 150, 120, 130), 2), W, H)
    assert summary.mode == "none" and summary.frames_with_face == 2


def test_two_large_faces_are_reported_as_two_people_left_to_right():
    frames = [[FaceBox(1300, 300, 300, 340, 0.9), FaceBox(300, 300, 300, 340, 0.9)] for _ in range(10)]
    summary = summarize_faces(frames, W, H)

    assert summary.mode == "two"
    assert summary.face[0] < summary.second[0]


def test_no_frames_means_no_faces():
    assert summarize_faces([], W, H).mode == "none"


def test_the_facecam_border_is_found_from_edges_that_never_move():
    """A facecam overlay has a fixed rectangular border while the game moves underneath it."""
    import cv2

    rng = np.random.default_rng(5)
    frames = []
    for _ in range(8):
        # moving gameplay: a different smooth picture every frame (real footage is smooth, not pixel noise)
        coarse = rng.integers(0, 255, (9, 16), dtype=np.uint8)
        frame = cv2.resize(coarse, (640, 360), interpolation=cv2.INTER_CUBIC)
        frame[40:160, 30:210] = 90                                      # the overlay (static, flat)
        frame[40:160, 30:32] = 255
        frame[40:160, 208:210] = 255
        frame[40:42, 30:210] = 255
        frame[158:160, 30:210] = 255
        frames.append(frame)
    guess = (60, 60, 120, 90)                                           # a rough box around the face

    x, y, w, h = refine_box_with_edges(frames, face=(100, 80, 40, 44), guess=guess)

    assert x == pytest.approx(30, abs=4) and y == pytest.approx(40, abs=4)
    assert x + w == pytest.approx(210, abs=4) and y + h == pytest.approx(160, abs=4)


# ------------------------------------------------------- the stream's own facecam inside the gameplay
def overlap(a, b) -> float:  # noqa: ANN001
    w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return max(0.0, w) * max(0.0, h)


def stream(cam_box: tuple[float, float, float, float]) -> FaceSummary:
    """A stream with its camera overlaid on the gameplay, the face inside that overlay."""
    bx, by, bw, bh = cam_box
    face = (bx + bw * 0.35, by + bh * 0.25, bw * 0.3, bh * 0.4)
    return FaceSummary(mode="facecam", face=face, facecam_box=cam_box, frames=10, frames_with_face=10)


@pytest.mark.parametrize("cam_box", [(0, 700, 560, 380), (1360, 700, 560, 380), (0, 0, 520, 380), (1400, 0, 520, 380)])
def test_the_gameplay_below_does_not_show_the_facecam_a_second_time(cam_box):  # noqa: ANN001
    plan = plan_layout(W, H, "9:16", "facecam_top", stream(cam_box))

    x, y, w, h = bottom = plan.bottom["crop"]
    assert overlap(bottom, cam_box) == 0
    assert x + 0.2 * w <= W / 2 <= x + 0.8 * w          # the middle of the screen (crosshair, character) stays in view
    assert "facecam" in plan.reason


def test_a_facecam_too_wide_to_slide_past_is_cropped_above_when_that_zooms_in_only_a_little():
    cam_box = (0, 760, 900, 320)

    plan = plan_layout(W, H, "9:16", "facecam_top", stream(cam_box))

    x, y, w, h = bottom = plan.bottom["crop"]
    assert overlap(bottom, cam_box) == 0 and y + h <= 760
    centred_width = plan_layout(W, H, "9:16", "facecam_top", stream(cam_box), gameplay_box=(0, 0, W, H)).bottom["crop"][2]
    assert w >= centred_width / 1.5


def test_when_nothing_leaves_the_facecam_out_without_losing_too_much_the_gameplay_stays_centred():
    cam_box = (0, 400, 900, 680)

    plan = plan_layout(W, H, "9:16", "facecam_top", stream(cam_box))

    assert plan.bottom["crop"] == plan_layout(W, H, "9:16", "facecam_top", stream(cam_box),
                                              gameplay_box=(0, 0, W, H)).bottom["crop"]


def test_a_gameplay_box_you_set_is_used_even_if_it_holds_the_facecam():
    cam_box = (0, 700, 560, 380)

    plan = plan_layout(W, H, "9:16", "facecam_top", stream(cam_box), gameplay_box=(0, 0, 1200, 1080))

    x, y, w, h = plan.bottom["crop"]
    assert x + w <= 1200 and x < 560                   # inside your box, not moved away from the camera
