"""Reframing: turning the source picture into the target shape without losing what matters.

Layouts
  fill         crop to the target shape around the subject (a face when one is found)
  fit_blur     the whole frame, untouched, over a blurred copy of itself (nothing is cut off: the safe
               choice for gameplay)
  facecam_top  the streamer's camera on top, the gameplay below
  split        two people stacked, one tile each
  auto         picks one of the above from what the face detector saw, and says why

The planning is pure geometry and is tested without video. Face detection (OpenCV YuNet) is optional:
without the model file every clip still renders, using layouts that need no faces.
"""
from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..config import get_settings
from ..services import ffmpeg

ASPECTS: dict[str, tuple[int, int]] = {"9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350),
                                       "16:9": (1920, 1080)}
LAYOUTS = ("auto", "fill", "fit_blur", "facecam_top", "split")
LAYOUT_LABELS = {"auto": "Automatic", "fill": "Crop to subject", "fit_blur": "Whole frame on blur",
                 "facecam_top": "Facecam over gameplay", "split": "Split screen (two people)"}
SAFE_TOP, SAFE_BOTTOM = 0.10, 0.80
BIG_FACE = 0.18        # a face wider than this share of the frame is a camera shot, not an overlay
YUNET_FILE = "face_detection_yunet_2023mar.onnx"

Box = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class FaceBox:
    x: float
    y: float
    w: float
    h: float
    score: float = 1.0


@dataclass
class FaceSummary:
    mode: str = "none"                    # none | cam | facecam | two
    face: tuple[float, float, float, float] | None = None
    second: tuple[float, float, float, float] | None = None
    facecam_box: tuple[float, float, float, float] | None = None
    frames: int = 0
    frames_with_face: int = 0
    detector: str = ""                    # "yunet", or "" when no detector was available

    def as_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "face": self.face, "second": self.second, "facecam_box": self.facecam_box,
                "frames": self.frames, "frames_with_face": self.frames_with_face, "detector": self.detector}


@dataclass
class ReframePlan:
    layout: str
    out_w: int
    out_h: int
    src_w: int
    src_h: int
    crop: Box | None = None                    # fill: x, y, w, h in source pixels
    fg: Box | None = None                      # fit_blur: where the untouched frame sits in the output
    top: dict[str, Any] | None = None          # stacked layouts: {"crop": Box, "height": int}
    bottom: dict[str, Any] | None = None
    caption_y: float = 0.70                    # suggested vertical centre of the captions (share of height)
    hook_y: float = 0.16                       # suggested vertical centre of the hook title
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"layout": self.layout, "out_w": self.out_w, "out_h": self.out_h, "crop": self.crop, "fg": self.fg,
                "top": self.top, "bottom": self.bottom, "caption_y": round(self.caption_y, 3),
                "hook_y": round(self.hook_y, 3), "reason": self.reason}


# ------------------------------------------------------------------------------------------- geometry
def _even(value: float) -> int:
    return max(2, int(value / 2 + 0.5) * 2)


def _floor_even(value: float) -> int:
    return max(2, int(value) // 2 * 2)


def _ceil_even(value: float) -> int:
    v = int(np.ceil(value))
    return v + (v % 2)


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def _aspect_crop(region: tuple[float, float, float, float], ratio: float, cx: float | None = None,
                 cy: float | None = None, zoom: float = 1.0) -> Box:
    """The largest box of the given width/height ratio inside region, shrunk by zoom, centred on (cx, cy)
    as far as the region allows. Every number is even and the box never leaves the region."""
    rx, ry = _ceil_even(region[0]), _ceil_even(region[1])
    rw = _floor_even(region[0] + region[2] - rx)
    rh = _floor_even(region[1] + region[3] - ry)
    if rw / rh > ratio:
        h = rh / zoom
        w = h * ratio
    else:
        w = rw / zoom
        h = w / ratio
    w, h = min(_even(w), rw), min(_even(h), rh)
    cx = rx + rw / 2 if cx is None else cx
    cy = ry + rh / 2 if cy is None else cy
    # positions may be 0, unlike sizes; rx/ry and the sizes are even, so rounding down stays inside
    x = int(_clamp(cx - w / 2, rx, rx + rw - w)) // 2 * 2
    y = int(_clamp(cy - h / 2, ry, ry + rh - h)) // 2 * 2
    return max(rx, x), max(ry, y), w, h


def _centre(box: tuple[float, float, float, float]) -> tuple[float, float]:
    return box[0] + box[2] / 2, box[1] + box[3] / 2


def default_facecam_box(face: tuple[float, float, float, float], src_w: int, src_h: int) -> tuple[float, ...]:
    """Typical webcam framing around a face, used when the overlay's border cannot be found."""
    fx, fy, fw, fh = face
    cw = min(src_w, fw * 3.4)
    ch = min(src_h, cw * 0.75)
    left = _clamp(fx + fw / 2 - cw / 2, 0, src_w - cw)
    top = _clamp(fy + fh * 0.45 - ch * 0.45, 0, src_h - ch)
    return left, top, cw, ch


# --------------------------------------------------------------------------------------------- planning
def plan_layout(src_w: int, src_h: int, aspect: str, layout: str, faces: FaceSummary | None, *,
                subject_x: float | None = None, subject_y: float | None = None, zoom: float = 1.0,
                facecam_box: tuple[float, ...] | None = None, gameplay_box: tuple[float, ...] | None = None,
                preset: str = "auto") -> ReframePlan:
    out_w, out_h = ASPECTS.get(aspect, ASPECTS["9:16"])
    faces = faces or FaceSummary()
    ratio = out_w / out_h
    zoom = _clamp(zoom, 1.0, 3.0)
    whole = (0.0, 0.0, float(src_w), float(src_h))
    same_shape = abs((src_w / src_h) / ratio - 1.0) < 0.02
    cam_box = facecam_box or faces.facecam_box
    manual_subject = subject_x is not None
    plan = ReframePlan(layout=layout, out_w=out_w, out_h=out_h, src_w=src_w, src_h=src_h)

    two_fit = False
    if faces.mode == "two" and faces.face and faces.second:
        span = max(faces.face[0] + faces.face[2], faces.second[0] + faces.second[2]) - min(faces.face[0], faces.second[0])
        two_fit = span <= _aspect_crop(whole, ratio)[2] * 0.9

    # ---- choose -----------------------------------------------------------------------------------
    chosen, reason = layout, ""
    if layout == "auto":
        if same_shape:
            chosen, reason = "fill", "The source already has this shape, so the whole frame is used."
        elif cam_box and (faces.mode == "facecam" or facecam_box):
            chosen, reason = "facecam_top", "A small face stays in one corner: treated as a facecam over gameplay."
        elif faces.mode == "cam" and not (preset == "gaming" and faces.face and faces.face[2] < 0.22 * src_w):
            chosen, reason = "fill", "One face fills much of the frame: cropped around it."
        elif faces.mode == "two":
            chosen = "fill" if two_fit else "split"
            reason = ("Two people sit close enough to share one crop." if two_fit
                      else "Two people are too far apart for one crop: split screen.")
        else:
            chosen = "fit_blur"
            reason = ("No face was found, so the whole frame is kept visible." if faces.detector or faces.frames
                      else "Face detection did not run, so the whole frame is kept visible.")
    elif layout == "facecam_top" and not cam_box:
        chosen, reason = "fit_blur", "No facecam was found for the stacked layout: showing the whole frame instead."
    elif layout == "split" and not (faces.face and faces.second):
        chosen = "fill" if faces.face else "fit_blur"
        reason = "Split screen needs two faces; fewer were found."
    plan.layout = chosen
    plan.reason = reason or f"Layout chosen by you: {LAYOUT_LABELS.get(chosen, chosen)}."

    # ---- build ------------------------------------------------------------------------------------
    if chosen == "fill":
        cx = cy = None
        if manual_subject:
            cx = _clamp(subject_x, 0.0, 1.0) * src_w
            cy = None if subject_y is None else _clamp(subject_y, 0.0, 1.0) * src_h
        elif faces.mode == "two" and faces.face and faces.second and two_fit:
            cx = (min(faces.face[0], faces.second[0])
                  + max(faces.face[0] + faces.face[2], faces.second[0] + faces.second[2])) / 2
        elif faces.face and faces.mode in ("cam", "two", "facecam"):
            cx, face_cy = _centre(faces.face)
            cy = face_cy + 0.3 * faces.face[3] if zoom > 1.0 else None
        plan.crop = (0, 0, _floor_even(src_w), _floor_even(src_h)) if (same_shape and zoom == 1.0) \
            else _aspect_crop(whole, ratio, cx, cy, zoom)
        plan.caption_y, plan.hook_y = 0.70, 0.16

    elif chosen == "fit_blur":
        scale = min(out_w / src_w, out_h / src_h)
        fw, fh = min(out_w, _even(src_w * scale)), min(out_h, _even(src_h * scale))
        fx, fy = _floor_even((out_w - fw) / 2) if out_w > fw else 0, _floor_even((out_h - fh) / 2) if out_h > fh else 0
        plan.fg = (fx, fy, fw, fh)
        below = (fy + fh) / out_h
        plan.caption_y = _clamp(below + 0.07, SAFE_TOP, SAFE_BOTTOM) if below < 0.76 else SAFE_BOTTOM
        plan.hook_y = _clamp(fy / out_h - 0.07, 0.08, 0.60) if fy / out_h > 0.16 else 0.12

    elif chosen == "facecam_top":
        bx, by, bw, bh = cam_box
        top_h = int(_clamp(_even(out_w * bh / bw), _even(out_h * 0.29), _even(out_h * 0.5)))
        bot_h = out_h - top_h
        face_cx = _centre(faces.face)[0] if (faces.face and not facecam_box) else None
        plan.top = {"crop": _aspect_crop((bx, by, bw, bh), out_w / top_h, face_cx), "height": top_h}
        plan.bottom = {"crop": _aspect_crop(gameplay_box or whole, out_w / bot_h), "height": bot_h}
        plan.caption_y = _clamp((top_h + 0.58 * bot_h) / out_h, SAFE_TOP, SAFE_BOTTOM)
        plan.hook_y = _clamp(top_h / out_h, 0.05, 0.60)

    else:  # split
        top_h = _floor_even(out_h / 2)
        bot_h = out_h - top_h
        first, second = sorted((faces.face, faces.second), key=lambda f: f[0])
        tiles = []
        for face, tile_h in ((first, top_h), (second, bot_h)):
            fcx, fcy = _centre(face)
            height = _clamp(face[3] * 3.0, src_h * 0.5, src_h)
            tile_ratio = out_w / tile_h
            width = min(src_w, height * tile_ratio)
            height = width / tile_ratio
            region = (_clamp(fcx - width / 2, 0, src_w - width), _clamp(fcy + 0.15 * face[3] - height / 2, 0, src_h - height),
                      width, height)
            tiles.append({"crop": _aspect_crop(region, tile_ratio), "height": tile_h})
        plan.top, plan.bottom = tiles
        plan.caption_y, plan.hook_y = 0.50, 0.12
    return plan


def build_filter(plan: ReframePlan, source: str = "[vin]", out: str = "[base]") -> str:
    """The FFmpeg filter chain that produces the output picture (before captions) from the source."""
    w, h = plan.out_w, plan.out_h
    if plan.layout == "fill":
        x, y, cw, ch = plan.crop
        return f"{source}crop={cw}:{ch}:{x}:{y},scale={w}:{h}:flags=lanczos,setsar=1{out}"
    if plan.layout == "fit_blur":
        fx, fy, fw, fh = plan.fg
        bw, bh = _even(w / 4), _even(h / 4)  # blur a quarter-size copy: far cheaper, looks the same
        return (f"{source}split=2[rf_bg][rf_fg];"
                f"[rf_bg]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},boxblur=10:2,"
                f"eq=brightness=-0.12,scale={w}:{h}:flags=bilinear[rf_bgs];"
                f"[rf_fg]scale={fw}:{fh}:flags=lanczos[rf_fgs];"
                f"[rf_bgs][rf_fgs]overlay={fx}:{fy},setsar=1{out}")
    tx, ty, tw, th = plan.top["crop"]
    bx, by, bw, bh = plan.bottom["crop"]
    return (f"{source}split=2[rf_a][rf_b];"
            f"[rf_a]crop={tw}:{th}:{tx}:{ty},scale={w}:{plan.top['height']}:flags=lanczos[rf_top];"
            f"[rf_b]crop={bw}:{bh}:{bx}:{by},scale={w}:{plan.bottom['height']}:flags=lanczos[rf_bot];"
            f"[rf_top][rf_bot]vstack=inputs=2,setsar=1{out}")


# ----------------------------------------------------------------------------------------- face summary
def summarize_faces(per_frame: list[list[FaceBox]], src_w: int, src_h: int) -> FaceSummary:
    """Turn per-frame detections into one statement about the clip: no face, one camera face, a small
    facecam overlay, or two people."""
    frames = len(per_frame)
    with_face = sum(1 for boxes in per_frame if boxes)
    summary = FaceSummary(frames=frames, frames_with_face=with_face)
    clusters: list[dict[str, Any]] = []
    for index, boxes in enumerate(per_frame):
        for box in boxes:
            cx, cy = box.x + box.w / 2, box.y + box.h / 2
            home = next((c for c in clusters if abs(c["cx"] - cx) < src_w * 0.06 and abs(c["cy"] - cy) < src_h * 0.08),
                        None)
            if home is None:
                clusters.append({"cx": cx, "cy": cy, "boxes": [box], "frames": {index}})
            else:
                home["boxes"].append(box)
                home["frames"].add(index)
                home["cx"] = statistics.fmean(b.x + b.w / 2 for b in home["boxes"])
                home["cy"] = statistics.fmean(b.y + b.h / 2 for b in home["boxes"])
    needed = max(3, 0.4 * frames)
    steady = sorted((c for c in clusters if len(c["frames"]) >= needed), key=lambda c: -len(c["frames"]))
    if not steady:
        return summary

    def median_box(cluster: dict[str, Any]) -> tuple[float, float, float, float]:
        boxes = cluster["boxes"]
        return tuple(round(statistics.median(getattr(b, key) for b in boxes), 1) for key in ("x", "y", "w", "h"))

    boxes = [median_box(c) for c in steady]
    main = boxes[0]
    partner = next((b for b in boxes[1:] if b[2] >= 0.06 * src_w and 0.5 <= b[2] / main[2] <= 2.0), None)
    if partner is not None and main[2] >= 0.06 * src_w:
        summary.mode = "two"
        summary.face, summary.second = sorted((main, partner), key=lambda b: b[0])
        return summary
    summary.face = main
    cx, cy = _centre(main)
    near_edge = cx < 0.3 * src_w or cx > 0.7 * src_w or cy < 0.3 * src_h or cy > 0.7 * src_h
    if main[2] >= BIG_FACE * src_w or not near_edge:
        summary.mode = "cam"
    else:
        summary.mode = "facecam"
        summary.facecam_box = tuple(round(v, 1) for v in default_facecam_box(main, src_w, src_h))
    return summary


def refine_box_with_edges(frames: list[np.ndarray], face: tuple[float, float, float, float],
                          guess: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    """Find a facecam overlay's border. The overlay's edges are straight lines that never move, while the
    game underneath changes: taking the median of each pixel's edge strength over several frames keeps
    only the edges that stay put. (Technique from the GTA 6 Clipper reference.)"""
    stack = [f.astype(np.float32) for f in frames]
    gx = np.median(np.stack([np.abs(np.diff(f, axis=1)) for f in stack]), axis=0)  # vertical edges
    gy = np.median(np.stack([np.abs(np.diff(f, axis=0)) for f in stack]), axis=0)  # horizontal edges
    height, width = stack[0].shape
    fx, fy, fw, fh = face
    y0, y1 = int(max(0, fy - fh * 0.5)), int(min(height, fy + fh * 1.5))
    x0, x1 = int(max(0, fx - fw * 0.5)), int(min(width, fx + fw * 1.5))
    columns = gx[y0:y1].mean(axis=0)
    rows = gy[:, x0:x1].mean(axis=1)

    def scan(profile: np.ndarray, start: float, stop: float) -> int | None:
        step = 1 if stop > start else -1
        indexes = [i for i in range(int(start), int(stop), step) if 0 <= i < profile.size]
        if len(indexes) < 5:
            return None
        values = profile[indexes]
        best = int(np.argmax(values))
        if values[best] > 3.5 * (float(np.median(values)) + 1e-3) and values[best] > 6:
            return indexes[best] + 1
        return None

    left = scan(columns, fx - fw * 0.15, fx - fw * 2.6)
    right = scan(columns, fx + fw * 1.15, fx + fw * 3.6)
    top = scan(rows, fy - fh * 0.15, fy - fh * 2.2)
    bottom = scan(rows, fy + fh * 1.15, fy + fh * 3.2)
    gx0, gy0, gw, gh = guess
    left = gx0 if left is None else left
    right = gx0 + gw if right is None else right
    top = gy0 if top is None else top
    bottom = gy0 + gh if bottom is None else bottom
    left, top = max(0, int(left)), max(0, int(top))
    right, bottom = min(width, int(right)), min(height, int(bottom))
    if right - left < fw * 1.4 or bottom - top < fh * 1.4:  # implausibly tight: keep the guess
        return int(gx0), int(gy0), int(gw), int(gh)
    return left, top, right - left, bottom - top


# -------------------------------------------------------------------------------------- face detection
def yunet_model_path() -> Path:
    return get_settings().models_dir / "yunet" / YUNET_FILE


def detector_available() -> bool:
    try:
        import cv2
    except ImportError:
        return False
    return hasattr(cv2, "FaceDetectorYN") and yunet_model_path().exists()


def analyze_faces(video_path: Path, start: float, duration: float, src_w: int, src_h: int, *,
                  max_samples: int = 24, should_cancel: Callable[[], bool] | None = None) -> FaceSummary:
    """Sample frames from the clip, detect faces, and summarise. Returns mode "none" with an empty
    detector name when the detector is not installed."""
    if not detector_available() or duration <= 0:
        return FaceSummary()
    import cv2

    width = min(960, _floor_even(src_w))
    height = _even(width * src_h / src_w)
    scale = src_w / width
    fps = min(2.0, max(0.2, max_samples / duration))
    detector = cv2.FaceDetectorYN.create(str(yunet_model_path()), "", (width, height), 0.75, 0.3, 50)
    per_frame: list[list[FaceBox]] = []
    grey: list[np.ndarray] = []
    for frame in ffmpeg.iter_frames(video_path, fps=fps, width=width, height=height, pix_fmt="bgr24", start=start,
                                    duration=duration, should_cancel=should_cancel):
        _, found = detector.detect(np.ascontiguousarray(frame))
        boxes = []
        if found is not None:
            for row in found:
                boxes.append(FaceBox(float(row[0]) * scale, float(row[1]) * scale, float(row[2]) * scale,
                                     float(row[3]) * scale, float(row[-1])))
        per_frame.append(boxes)
        if len(grey) < 12:
            grey.append(frame.mean(axis=2).astype(np.uint8))
        if len(per_frame) >= max_samples:
            break
    summary = summarize_faces(per_frame, src_w, src_h)
    summary.detector = "yunet"
    if summary.mode == "facecam" and summary.face and len(grey) >= 4:
        small = tuple(v / scale for v in summary.face)
        guess = tuple(v / scale for v in summary.facecam_box)
        x, y, w, h = refine_box_with_edges(grey, small, guess)
        summary.facecam_box = (round(x * scale, 1), round(y * scale, 1), round(w * scale, 1), round(h * scale, 1))
    return summary
