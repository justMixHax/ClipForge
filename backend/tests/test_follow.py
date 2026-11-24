"""Following the person: in the cropped layout the camera can move with the person instead of standing still.
It behaves like a heavy tripod: it stays put while the person is near the middle, moves over slowly when they
have really gone somewhere else, and ignores a detection that jumps for a moment."""
from __future__ import annotations

import pytest

from app.editing import follow
from app.editing.follow import camera_path, clip_points, crop_x_expression, position_at, track_face
from app.editing.reframe import FaceBox, ReframePlan, build_filter

SRC_W, CROP_W = 1920, 608          # a 16:9 source cropped to 9:16


def samples(*pieces: tuple[float, float, float | None], step: float = 0.25) -> list[tuple[float, float | None]]:
    """(from, to, face centre) stretches, sampled four times a second."""
    out = []
    for start, end, centre in pieces:
        t = start
        while t < end - 1e-9:
            out.append((round(t, 3), centre))
            t += step
    return out


# ----------------------------------------------------------------------------------------- the camera path
def test_a_person_who_stays_put_gives_a_camera_that_never_moves():
    path = camera_path(samples((0, 20, 960)), crop_w=CROP_W, src_w=SRC_W)

    assert path == [(0.0, 656)]                                   # centred on them: 960 - 608 / 2


def test_small_movements_inside_the_middle_do_not_move_the_camera():
    wobble = [(t, 960 + (60 if int(t * 4) % 2 else -60)) for t, _ in samples((0, 20, 960))]

    assert len(camera_path(wobble, crop_w=CROP_W, src_w=SRC_W)) == 1


def test_when_the_person_walks_away_the_camera_follows_slowly_and_stops_on_them():
    path = camera_path(samples((0, 5, 700), (5, 15, 1000)), crop_w=CROP_W, src_w=SRC_W)

    assert path[0] == (0.0, 396)
    (t0, x0), (t1, x1) = path[1], path[2]
    assert len(path) == 3 and x0 == 396 and x1 == 696             # from them to them: 1000 - 304
    assert 5.4 <= t0 <= 6.0                                        # only after the move was seen a few times
    assert 1.5 <= t1 - t0 <= 5.0                                   # a slow pan, not a jump


def test_a_detection_that_jumps_for_a_moment_is_ignored():
    blip = samples((0, 5, 700), (5, 5.5, 1500), (5.5, 12, 700))

    assert len(camera_path(blip, crop_w=CROP_W, src_w=SRC_W)) == 1


def test_frames_without_a_face_hold_the_camera_where_it_is():
    path = camera_path(samples((0, 5, 700), (5, 10, None), (10, 15, 700)), crop_w=CROP_W, src_w=SRC_W)

    assert len(path) == 1


def test_a_far_move_is_made_faster_than_a_near_one():
    near = camera_path(samples((0, 5, 600), (5, 20, 800)), crop_w=CROP_W, src_w=SRC_W)
    far = camera_path(samples((0, 5, 400), (5, 20, 1500)), crop_w=CROP_W, src_w=SRC_W)

    speed = lambda p: abs(p[2][1] - p[1][1]) / (p[2][0] - p[1][0])  # noqa: E731
    assert speed(far) > 2 * speed(near)


def test_the_crop_never_leaves_the_picture_and_is_always_even():
    path = camera_path(samples((0, 5, 100), (5, 15, 1900)), crop_w=CROP_W, src_w=SRC_W)

    assert all(0 <= x <= SRC_W - CROP_W and x % 2 == 0 for _, x in path)
    assert path[0][1] == 0 and path[-1][1] == SRC_W - CROP_W


def test_no_face_at_all_gives_no_path():
    assert camera_path(samples((0, 10, None)), crop_w=CROP_W, src_w=SRC_W) == []
    assert camera_path([], crop_w=CROP_W, src_w=SRC_W) == []


# ------------------------------------------------------------------------------------- which face to follow
def test_the_face_followed_is_the_same_person_from_frame_to_frame():
    person = lambda cx: FaceBox(cx - 100, 300, 200, 200)  # noqa: E731
    other = FaceBox(1500, 300, 200, 200)
    frames = [(0.0, [person(600), other]), (0.25, [other, person(640)]), (0.5, [other]), (0.75, [person(700), other])]

    tracked = track_face(frames, main=(500, 300, 200, 200), src_w=SRC_W)

    assert tracked == [(0.0, 600), (0.25, 640), (0.5, None), (0.75, 700)]


def test_a_much_smaller_face_nearby_is_not_taken_for_the_person():
    frames = [(0.0, [FaceBox(590, 300, 40, 40)])]                  # a face on a poster behind them

    assert track_face(frames, main=(500, 300, 200, 200), src_w=SRC_W) == [(0.0, None)]


# ----------------------------------------------------------------------------- where the camera is at a time
PATH = [(10.0, 400), (15.0, 400), (17.0, 800)]


def test_the_position_at_a_time_is_read_off_the_path():
    assert position_at(PATH, 0.0) == 400 and position_at(PATH, 15.0) == 400
    assert position_at(PATH, 16.0) == 600 and position_at(PATH, 99.0) == 800
    assert position_at([], 5.0) is None


def test_a_clip_played_in_one_piece_gets_the_path_in_its_own_time():
    assert clip_points(PATH, [(10.0, 20.0)]) == [(0.0, 400), (5.0, 400), (7.0, 800)]


def test_a_part_left_out_makes_the_camera_cut_instead_of_pan():
    points = clip_points(PATH, [(10.0, 15.5), (16.5, 20.0)])       # the middle of the pan is cut out

    assert points == [(0.0, 400), (5.0, 400), (5.5, 500), (5.5, 700), (6.0, 800)]


def test_a_clip_that_starts_after_the_last_move_gets_a_camera_that_stands_still():
    assert clip_points(PATH, [(18.0, 20.0)]) == [(0.0, 800)]


# ------------------------------------------------------------------------------------------ in the render
def test_one_position_is_a_plain_number_and_moves_become_an_expression_of_time():
    assert crop_x_expression([(0.0, 400)]) == "400"
    moving = crop_x_expression([(0.0, 400), (5.0, 400), (7.0, 800)])
    assert moving == "400+400*clip((t-5)/2\\,0\\,1)"
    assert "gte(t\\,5.5)" in crop_x_expression([(0.0, 400), (5.5, 500), (5.5, 700)])


def test_the_cropped_layout_uses_the_moving_position_when_there_is_one():
    plan = ReframePlan(layout="fill", out_w=1080, out_h=1920, src_w=1920, src_h=1080, crop=(400, 0, 608, 1080))

    still = build_filter(plan)
    moving = build_filter(plan, points=[(0.0, 400), (5.0, 400), (7.0, 800)])

    assert "crop=608:1080:400:0" in still
    assert "crop=608:1080:400+400*clip((t-5)/2\\,0\\,1):0" in moving


@pytest.mark.parametrize("layout", ["fit_blur", "facecam_top"])
def test_other_layouts_are_not_touched_by_a_path(layout: str):
    plan = ReframePlan(layout=layout, out_w=1080, out_h=1920, src_w=1920, src_h=1080, fg=(0, 656, 1080, 608),
                       top={"crop": (0, 0, 640, 360), "height": 608}, bottom={"crop": (0, 0, 640, 776), "height": 1312})

    assert build_filter(plan, points=[(0.0, 400), (7.0, 800)]) == build_filter(plan)


def test_the_limits_keep_a_restless_clip_from_becoming_an_endless_expression():
    restless = [(float(i), 400 + 2 * (i % 2) * 200) for i in range(2 * follow.MAX_POINTS)]

    assert len(clip_points(restless, [(0.0, 1e6)])) <= follow.MAX_POINTS
