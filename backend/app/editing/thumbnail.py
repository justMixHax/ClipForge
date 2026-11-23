"""Thumbnails: one picture per clip that makes people want to watch it.

The best moment of the clip (sharp, well lit, a face clearly visible, close to the payoff), framed exactly like
the clip (camera over gameplay, or the cropped frame) but without the captions, with two to six big words on it and
one of them in colour. 1080x1920 for TikTok, Shorts and Reels covers. The words never cover a face (they go under
the camera, or above or below the face), and stay clear of the edges and of the bottom, where those apps put the
caption and buttons.

The words are the clip's own: Claude writes them from what is said when it picks the clip, or they are taken
from the clip's title. Nothing is promised that the clip does not show.
"""
from __future__ import annotations

import math
import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from ..analysis.describe import bare_word
from ..config import get_settings
from ..services import ffmpeg
from ..services.storage import replace_file
from .fonts import find_font
from .reframe import ReframePlan, build_filter, detector_available, yunet_model_path

SAMPLES = 16                              # candidate moments looked at per clip
HIGHLIGHT = (255, 212, 0)                 # the coloured word
WHITE = (255, 255, 255)
SAFE_LEFT, SAFE_RIGHT = 0.06, 0.94        # share of the width the words stay inside
SAFE_TOP, SAFE_BOTTOM = 0.06, 0.72        # the apps put the caption and buttons below this
MAX_LINES = 3
FONT_NAMES = ("Anton", "Bebas Neue", "Montserrat ExtraBold", "Noto Sans Black", "Impact", "Arial Black")
WIDE_FONTS = ("Arial", "Tahoma", "Segoe UI")                # Windows fonts with Greek, Cyrillic and more
CJK_FILES = ("YuGothB.ttc", "msyhbd.ttc", "malgunbd.ttf")   # Windows fonts for Japanese, Chinese and Korean
# Scripts that need text shaping or right-to-left layout, which this drawing does not do: Hebrew, Arabic,
# Syriac, Thaana, the Indic scripts, Thai, Lao, Tibetan, Myanmar, Khmer, and their presentation forms.
SHAPED = ((0x0590, 0x08FF), (0x0900, 0x0DFF), (0x0E00, 0x0FFF), (0x1000, 0x109F), (0x1780, 0x17FF),
          (0xFB1D, 0xFDFF), (0xFE70, 0xFEFF))
SMALLEST = 12                                               # pixels: a font is never made smaller than this

Box = tuple[float, float, float, float]   # x0, y0, x1, y1 in the picture


# ----------------------------------------------------------------------------------------------- the layout
@dataclass
class TextLayout:
    lines: list[str]
    size: int
    box: Box                                # the whole block of words
    line_height: int
    top_offset: int                         # how far below its origin a line's visible top sits
    stroke: int
    font_path: str | None


def _font_path() -> str | None:
    for name in FONT_NAMES:
        info = find_font(name)
        if info is not None:
            return str(info.path)
    return None


def _font(path: str | None, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size) if path else ImageFont.load_default(size=size)


def _candidates() -> list[str]:
    paths = [str(info.path) for name in (*FONT_NAMES, *WIDE_FONTS) if (info := find_font(name)) is not None]
    windows = os.environ.get("WINDIR", "")
    if windows:
        paths += [str(Path(windows) / "Fonts" / name) for name in CJK_FILES if (Path(windows) / "Fonts" / name).is_file()]
    return list(dict.fromkeys(paths))


@lru_cache(maxsize=32)
def _probe(path: str) -> tuple[ImageFont.FreeTypeFont, bytes]:
    """The font, and how it draws a character it does not have."""
    font = ImageFont.truetype(path, 40)
    return font, bytes(font.getmask("\uffff"))


def _covers(path: str, text: str) -> bool:
    try:
        font, missing = _probe(path)
    except OSError:
        return False
    return all(ch.isspace() or bytes(font.getmask(ch)) != missing for ch in set(text))


def font_for(text: str) -> str | None:
    """The first thumbnail font that has every character of the words, or None."""
    return next((path for path in _candidates() if _covers(path, text)), None)


def can_draw(text: str) -> bool:
    """Whether the words can be drawn properly here: none in a script that needs shaping, and a font that has
    every character. Words that cannot are left off the picture rather than shown as empty boxes."""
    if not text.strip() or any(lo <= ord(ch) <= hi for ch in text for lo, hi in SHAPED):
        return False
    return font_for(text) is not None


def _regions(plan: dict[str, Any] | None, height: int) -> list[tuple[float, float, bool]]:
    """Where the words may go, best first: (top, bottom, whether to sit at the top of it rather than in the middle).
    Stacked layouts: the top of the gameplay, right under the camera. Otherwise the upper part, else the lower."""
    low = SAFE_BOTTOM * height
    if plan and plan.get("layout") in ("facecam_top", "split") and plan.get("top"):
        seam = plan["top"]["height"] * height / max(1, plan.get("out_h") or height)
        return [(seam + 0.015 * height, low, True)]
    return [(SAFE_TOP * height, 0.42 * height, False), (0.40 * height, low, False)]


def _overlap(a: Box, b: Box) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return max(0.0, w) * max(0.0, h)


def layout_text(text: str, size: tuple[int, int], plan: dict[str, Any] | None,
                avoid: Sequence[Box] = ()) -> TextLayout:
    """The biggest size at which the words fit in at most three centred lines inside a region that keeps them off
    the faces in avoid (and off the camera, in a stacked layout)."""
    width, height = size
    words = text.split()
    max_width = (SAFE_RIGHT - SAFE_LEFT) * width
    path = font_for(text) or _font_path()
    measure = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    comfortable = int(0.04 * height)  # below this the words get hard to read; only a very long word goes there
    best: tuple[float, TextLayout] | None = None
    for region_top, region_bottom, at_top in _regions(plan, height):
        room = region_bottom - region_top
        layout = None
        for font_size in range(int(0.12 * height), SMALLEST - 1, -4):
            font = _font(path, font_size)
            stroke = max(4, int(font_size * 0.07))
            lines: list[str] = []
            for word in words:
                trial = f"{lines[-1]} {word}" if lines else word
                if lines and measure.textlength(trial, font=font) + 2 * stroke <= max_width:
                    lines[-1] = trial
                else:
                    lines.append(word)
            widths = [measure.textlength(line, font=font) + 2 * stroke for line in lines]
            top, bottom = font.getbbox("HGQ", stroke_width=stroke)[1], font.getbbox("HGQ", stroke_width=stroke)[3]
            line_height = int((bottom - top) * 1.06)
            block = line_height * min(len(lines), MAX_LINES)
            inside = len(lines) <= MAX_LINES and max(widths) <= max_width
            if (inside and block <= room) or (inside and font_size <= comfortable + 3) or font_size - 4 < SMALLEST:
                y0 = region_top if at_top else region_top + (room - block) / 2
                y0 = max(SAFE_TOP * height, min(y0, SAFE_BOTTOM * height - block))
                block_width = min(max(widths), max_width)
                x0 = (width - block_width) / 2
                layout = TextLayout(lines=lines[:MAX_LINES], size=font_size,
                                    box=(x0, y0, x0 + block_width, y0 + block), line_height=line_height,
                                    top_offset=top, stroke=stroke, font_path=path)
                break
        covered = sum(_overlap(layout.box, face) for face in avoid)
        if covered == 0:
            return layout
        if best is None or covered < best[0]:
            best = (covered, layout)
    return best[1]


def draw_cover(base: Image.Image, text: str, word: str, plan: dict[str, Any] | None,
               avoid: Sequence[Box] = ()) -> Image.Image:
    """The picture with the words on it: white with a heavy black outline, one word in colour, on a soft dark
    patch so they read on any picture."""
    image = base.convert("RGB")
    image = ImageEnhance.Contrast(image).enhance(1.08)
    image = ImageEnhance.Color(image).enhance(1.15)
    if not can_draw(text):
        return image  # no font here can draw these words properly: the picture alone, not empty boxes
    layout = layout_text(text, image.size, plan, avoid)
    x0, y0, x1, y1 = layout.box
    pad = layout.size * 0.35
    shade = Image.new("L", image.size, 0)
    ImageDraw.Draw(shade).rounded_rectangle((x0 - pad, y0 - pad, x1 + pad, y1 + pad), radius=int(pad), fill=140)
    shade = shade.filter(ImageFilter.GaussianBlur(pad * 0.8))
    image = Image.composite(Image.new("RGB", image.size, (0, 0, 0)), image, shade)

    draw = ImageDraw.Draw(image)
    font = _font(layout.font_path, layout.size)
    shadow = max(3, layout.size // 22)
    for index, line in enumerate(layout.lines):
        line_width = draw.textlength(line, font=font)
        x = (image.width - line_width) / 2
        y = y0 + index * layout.line_height - layout.top_offset
        draw.text((x + shadow, y + shadow), line, font=font, fill=(0, 0, 0), stroke_width=layout.stroke,
                  stroke_fill=(0, 0, 0))
        cursor = x
        for piece in line.split(" "):
            fill = HIGHLIGHT if word and bare_word(piece) == word else WHITE
            draw.text((cursor, y), piece, font=font, fill=fill, stroke_width=layout.stroke, stroke_fill=(0, 0, 0))
            cursor += draw.textlength(piece + " ", font=font)
    return image


# ------------------------------------------------------------------------------------- the moment and frame
def _detector(width: int, height: int) -> Any:
    if not detector_available():
        return None
    import cv2

    return cv2.FaceDetectorYN.create(str(yunet_model_path()), "", (width, height), 0.6, 0.3, 50)


def faces_in(image: Image.Image) -> list[Box]:
    """Where the faces are in a finished picture (empty without the face detector)."""
    scale = 540 / image.width
    small = np.asarray(image.convert("RGB").resize((540, max(2, int(image.height * scale)))))[:, :, ::-1]
    detector = _detector(small.shape[1], small.shape[0])
    if detector is None:
        return []
    _, found = detector.detect(np.ascontiguousarray(small))
    boxes = []
    for f in found if found is not None else []:
        x, y, w, h = (float(v) / scale for v in f[:4])
        boxes.append((x - 0.15 * w, y - 0.2 * h, x + 1.15 * w, y + 1.2 * h))  # with some room around the face
    return boxes


def frame_score(frame_bgr: np.ndarray, detector: Any) -> float:
    """How good a frame is for a thumbnail, 0 to about 1: sharp, lit, with contrast, and a face that is clearly
    visible (when the face detector is installed)."""
    import cv2

    grey = cv2.cvtColor(np.ascontiguousarray(frame_bgr), cv2.COLOR_BGR2GRAY)
    sharp = min(1.0, math.log1p(float(cv2.Laplacian(grey, cv2.CV_64F).var())) / math.log1p(1500.0))
    bright = float(grey.mean()) / 255.0
    light = 1.0 if 0.18 <= bright <= 0.85 else max(0.0, 1.0 - abs(bright - 0.5) * 2.5)
    contrast = min(1.0, float(grey.std()) / 64.0)
    face = 0.0
    if detector is not None:
        _, found = detector.detect(np.ascontiguousarray(frame_bgr))
        if found is not None and len(found):
            area = max(float(f[2] * f[3]) for f in found) / (frame_bgr.shape[0] * frame_bgr.shape[1])
            face = min(1.0, area * 12.0) * float(max(f[-1] for f in found))
    return 0.4 * sharp + 0.15 * light + 0.15 * contrast + 0.3 * face


def _visible(frame: np.ndarray, plan: dict[str, Any] | None, src_size: tuple[int, int]) -> np.ndarray:
    """The part of a small frame that the clip shows (the crop of a filled frame; all of it otherwise)."""
    if not plan or plan.get("layout") != "fill" or not plan.get("crop"):
        return frame
    scale = frame.shape[1] / max(1, src_size[0])
    x, y, w, h = (int(round(v * scale)) for v in plan["crop"])
    part = frame[max(0, y):y + max(2, h), max(0, x):x + max(2, w)]
    return part if part.size else frame


def pick_moment(video: Path, start: float, end: float, plan: dict[str, Any] | None, src_size: tuple[int, int], *,
                peak: float | None = None, samples: int = SAMPLES,
                should_cancel: Callable[[], bool] | None = None,
                inside: Sequence[Sequence[float]] | None = None) -> float:
    """The source time of the best frame between start and end; with inside (the parts the clip plays), only a
    frame the clip really shows."""
    length = end - start
    if length <= 0.4:
        return round(start, 3)
    lo, hi = start + 0.08 * length, end - 0.08 * length
    fps = samples / (hi - lo)
    width = 320
    height = max(2, int(round(width * src_size[1] / max(1, src_size[0]) / 2)) * 2)
    detector = None
    best_time, best_score = lo, -1.0
    for k, frame in enumerate(ffmpeg.iter_frames(video, fps=fps, width=width, height=height, pix_fmt="bgr24",
                                                 start=lo, duration=hi - lo, should_cancel=should_cancel)):
        part = _visible(frame, plan, src_size)
        if detector is None:
            detector = _detector(part.shape[1], part.shape[0])
        if detector is not None:
            detector.setInputSize((part.shape[1], part.shape[0]))
        moment = lo + k / fps
        if inside and not any(a <= moment <= b for a, b in inside):
            continue
        score = frame_score(part, detector)
        if peak is not None:
            score += 0.1 * max(0.0, 1.0 - abs(moment - peak) / max(1.0, length / 2))
        if score > best_score:
            best_time, best_score = moment, score
    if best_score < 0 and inside:  # no frame was sampled inside: the middle of the longest part played
        a, b = max(inside, key=lambda part: part[1] - part[0])
        return round((a + b) / 2, 3)
    return round(min(max(best_time, start), end), 3)


def _plan(plan: dict[str, Any]) -> ReframePlan:
    def box(value: Any) -> Any:
        return tuple(value) if value else None

    return ReframePlan(layout=plan["layout"], out_w=int(plan["out_w"]), out_h=int(plan["out_h"]), src_w=0, src_h=0,
                       crop=box(plan.get("crop")), fg=box(plan.get("fg")), top=plan.get("top"), bottom=plan.get("bottom"))


def compose_frame(video: Path, moment: float, plan: dict[str, Any], out_path: Path) -> Path:
    """The clip's picture at this source time, framed like the clip, without captions."""
    graph = build_filter(_plan(plan), source="[0:v]", out="[v]")
    cmd = [get_settings().ffmpeg_path, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
           "-ss", f"{max(0.0, moment):.3f}", "-i", str(video), "-filter_complex", graph, "-map", "[v]",
           "-frames:v", "1", str(out_path)]
    done = subprocess.run(cmd, capture_output=True, text=True, creationflags=ffmpeg.NO_WINDOW)
    if done.returncode != 0 or not out_path.is_file():
        raise ffmpeg.FFmpegError("The thumbnail frame could not be made", done.returncode,
                                 (done.stderr or "").strip()[-300:])
    return out_path


def make_cover(video: Path, start: float, end: float, plan: dict[str, Any], src_size: tuple[int, int], text: str,
               word: str, folder: Path, *, at: float | None = None, peak: float | None = None,
               should_cancel: Callable[[], bool] | None = None,
               inside: Sequence[Sequence[float]] | None = None) -> tuple[Path, float]:
    """Make the clip's thumbnail.jpg in folder. Returns (path, the source time used): the moment you chose (at),
    else the best one found."""
    moment = round(at, 3) if at is not None else pick_moment(video, start, end, plan, src_size, peak=peak,
                                                             should_cancel=should_cancel, inside=inside)
    folder.mkdir(parents=True, exist_ok=True)
    frame = compose_frame(video, moment, plan, folder / "thumbnail.frame.png")
    try:
        with Image.open(frame) as base:
            picture = base.convert("RGB")
        stacked = plan.get("layout") in ("facecam_top", "split")
        image = draw_cover(picture, text, word, plan, avoid=[] if stacked else faces_in(picture))
        temp, out = folder / "thumbnail.tmp.jpg", folder / "thumbnail.jpg"
        image.save(temp, "JPEG", quality=90, optimize=True)
        replace_file(temp, out)
    finally:
        frame.unlink(missing_ok=True)
    return out, moment
