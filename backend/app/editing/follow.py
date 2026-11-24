"""Following the person: a camera that moves with them in the cropped layout, instead of one fixed crop.

The camera behaves like a heavy tripod. It stands still while the person is near the middle of the frame, moves
over at a steady pace once they have really gone somewhere else, and ignores a detection that jumps for a moment
(a second face, a false find). A move has to be seen several times in a row before the camera follows it. The
idea and its measured thresholds come from OpenShorts' "SmoothedCameraman" (MIT licence, github.com/mutonby/
openshorts); the code here is ClipForge's own: the path is worked out once, as a few points in time, and FFmpeg
moves the crop along it in the same single render.

Only sideways: a wide picture cropped to a tall one has room to move left and right, not up and down.
"""
from __future__ import annotations

import statistics
from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np

from ..services import ffmpeg
from .reframe import FaceBox, _floor_even, detector_available, yunet_model_path

SAFE_ZONE = 0.25        # the person may move this share of the crop's width from its middle before it follows
CONFIRM = 3             # a move must be seen this many samples in a row: one stray detection never moves the frame
SLOW, FAST = 0.2, 0.9   # crop widths a second: a steady pan, and a quick one when they have left the frame
FAR = 0.5               # further than this share of the crop's width from the middle, they are out of the frame
MIN_MOVE_SECONDS = 0.3
SAMPLES_A_SECOND = 4.0
MAX_SAMPLES = 720       # a three-minute clip at four a second
MAX_POINTS = 240        # more turns than this in one clip is a detector gone wrong: the camera stands still

Sample = tuple[float, float | None]     # (source time, the person's centre from the left in source pixels, or None)
Point = tuple[float, int]               # (time, the crop's left edge in source pixels)


def track_face(frames: Sequence[tuple[float, Sequence[FaceBox]]], *, main: Sequence[float],
               src_w: int) -> list[Sample]:
    """Where the person is in each sampled frame: the face nearest to where they were last, of about their size.
    Another person, or a face on a poster, is not taken for them; a frame without them gives None."""
    last = main[0] + main[2] / 2
    width = main[2]
    out: list[Sample] = []
    for time, boxes in frames:
        near = [(abs(b.x + b.w / 2 - last), b) for b in boxes if 0.5 * width <= b.w <= 2.0 * width]
        near = [(d, b) for d, b in near if d <= 0.25 * src_w]
        if not near:
            out.append((time, None))
            continue
        box = min(near, key=lambda pair: pair[0])[1]
        last = box.x + box.w / 2
        out.append((time, last))
    return out


def camera_path(samples: Sequence[Sample], *, crop_w: int, src_w: int) -> list[Point]:
    """The camera's path as points in time: it holds its place between points with the same position and moves
    steadily between points with different ones. One point: it never moves. None: the person was never found."""
    seen = [(t, c) for t, c in samples if c is not None]
    if not seen:
        return []
    low, high = crop_w / 2, src_w - crop_w / 2

    def edge(centre: float) -> int:
        return int(min(max(centre, low), high) - crop_w / 2) // 2 * 2

    first = seen[0][0]
    centre = min(max(statistics.median(c for t, c in seen if t <= first + 1.0), low), high)
    path: list[Point] = [(round(samples[0][0], 3), edge(centre))]
    radius = SAFE_ZONE * crop_w
    pending: float | None = None
    count, busy_until = 0, float("-inf")
    for time, target in seen:
        if time < busy_until:
            continue
        if abs(target - centre) <= radius:
            pending, count = None, 0
            continue
        # outside the middle: the same move has to show up again before it is believed
        if pending is not None and abs(target - pending) <= radius:
            count += 1
        else:
            count = 1
        pending = target
        if count < CONFIRM:
            continue
        goal = min(max(pending, low), high)
        pending, count = None, 0
        if edge(goal) == path[-1][1]:
            continue  # already as far as the picture goes
        distance = abs(goal - centre)
        seconds = max(MIN_MOVE_SECONDS, distance / ((FAST if distance > FAR * crop_w else SLOW) * crop_w))
        path += [(round(time, 3), path[-1][1]), (round(time + seconds, 3), edge(goal))]
        centre, busy_until = goal, time + seconds
    return path


def position_at(path: Sequence[Sequence[float]], time: float) -> int | None:
    """The crop's left edge at a time on the path (an even number of pixels); None without a path."""
    if not path:
        return None
    if time <= path[0][0]:
        return int(path[0][1])
    for (t0, x0), (t1, x1) in zip(path, path[1:]):
        if time <= t1:
            share = 0.0 if t1 <= t0 else (time - t0) / (t1 - t0)
            return int(round((x0 + (x1 - x0) * share) / 2)) * 2
    return int(path[-1][1])


def clip_points(path: Sequence[Sequence[float]], segments: Sequence[Sequence[float]]) -> list[Point]:
    """The path in the clip's own time (0 is its first frame), for the parts of the source the clip plays. Where
    a part is left out the camera cuts to its new place, as the picture does."""
    if not path:
        return []
    points: list[Point] = []
    offset = 0.0
    for start, end in segments:
        inside = [(t, int(x)) for t, x in path if start < t < end]
        for t, x in [(start, position_at(path, start)), *inside, (end, position_at(path, end))]:
            points.append((round(offset + t - start, 3), x))
        offset += end - start
    tidy: list[Point] = []
    for index, point in enumerate(points):  # a point in the middle of a hold says nothing
        held = 0 < index < len(points) - 1 and points[index - 1][1] == point[1] == points[index + 1][1]
        if not held:
            tidy.append(point)
    while len(tidy) > 1 and tidy[-1][1] == tidy[-2][1]:
        tidy.pop()
    if len(tidy) > MAX_POINTS:
        return [(0.0, int(statistics.median(x for _, x in tidy)) // 2 * 2)]
    return tidy


def crop_x_expression(points: Sequence[Sequence[float]]) -> str:
    """The crop's left edge as an FFmpeg expression of the clip's time: the first position plus one ramp for
    every move (and one step for every cut). Commas are escaped for use inside a filter graph."""
    text = f"{int(points[0][1])}"
    for (t0, x0), (t1, x1) in zip(points, points[1:]):
        delta = int(x1 - x0)
        if delta == 0:
            continue
        sign, size = ("+" if delta > 0 else "-"), abs(delta)
        if t1 - t0 < 1e-3:
            text += f"{sign}{size}*gte(t\\,{t0:g})"
        else:
            text += f"{sign}{size}*clip((t-{t0:g})/{t1 - t0:g}\\,0\\,1)"
    return text


def follow_path(video: Path, start: float, duration: float, src_w: int, src_h: int, *,
                main: Sequence[float], crop_w: int,
                should_cancel: Callable[[], bool] | None = None) -> list[Point]:
    """Watch the clip a few times a second and work out the camera's path (in source time). Empty when the face
    detector is not installed or the person was not found."""
    if not detector_available() or duration <= 0 or crop_w >= src_w:
        return []
    import cv2

    width = min(960, _floor_even(src_w))
    height = max(2, int(width * src_h / src_w / 2 + 0.5) * 2)
    scale = src_w / width
    fps = min(SAMPLES_A_SECOND, MAX_SAMPLES / duration)
    detector = cv2.FaceDetectorYN.create(str(yunet_model_path()), "", (width, height), 0.75, 0.3, 50)
    frames: list[tuple[float, list[FaceBox]]] = []
    for index, frame in enumerate(ffmpeg.iter_frames(video, fps=fps, width=width, height=height, pix_fmt="bgr24",
                                                     start=start, duration=duration, should_cancel=should_cancel)):
        _, found = detector.detect(np.ascontiguousarray(frame))
        boxes = [] if found is None else [
            FaceBox(float(r[0]) * scale, float(r[1]) * scale, float(r[2]) * scale, float(r[3]) * scale, float(r[-1]))
            for r in found]
        frames.append((start + index / fps, boxes))
    return camera_path(track_face(frames, main=main, src_w=src_w), crop_w=crop_w, src_w=src_w)
