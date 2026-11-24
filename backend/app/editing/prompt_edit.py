"""Prompt edit: you describe in your own words what you want changed in a clip, Claude turns that into changes
ClipForge can make, and the clip is rendered again.

Claude answers with data only: it runs without tools (see analysis/claude_review.command), and every value is
checked here before anything is saved, within the same limits as the editor's own controls. It can change the cut,
leave parts out, the subtitles (style, font, size, height, colours, capitals, words per line, masking, a word's
spelling), the hook title, the layout, the sound, the title and the thumbnail. What ClipForge cannot do (music,
sound effects, speed changes, stickers...) is said, never faked. The clip as it was is kept with it, so one press
undoes the change."""
from __future__ import annotations

import copy
import dataclasses
import io
import json
import math
import re
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..analysis import claude_review
from ..analysis.answers import json_answers
from ..analysis.describe import clean_words
from ..errors import ClipForgeError, DependencyMissing
from ..services import ffmpeg
from .captions import PRESET_LABELS, PRESETS, CaptionWord, resolve_style
from .reframe import ASPECTS, LAYOUT_LABELS, LAYOUTS
from .thumbnail import can_draw

INSTRUCTION_MAX = 1000
MIN_SECONDS = 1.0            # the shortest clip a prompt edit leaves
CONTEXT_SECONDS = 120.0      # transcript shown before and after the clip, so it can start earlier or end later
TRANSCRIPT_BUDGET = 16000    # characters of transcript in the request at most
MAX_CUTS = 30
ASK_TIMEOUT = 240.0
# what a prompt edit can change on a clip, and what undo puts back
EDITABLE = ("start", "end", "settings", "hook_text", "title", "captions", "cover_text", "cover_word", "cover_time",
            "transcript_text")
KNOWN = ("start", "end", "leave_out", "tighten_pauses", "subtitles", "fix_words", "hook", "layout", "sound", "title",
         "thumbnail")
SUBTITLE_KEYS = ("enabled", "style", "font", "size", "position", "color", "highlight", "emphasis", "uppercase",
                 "words_per_line", "animation", "box", "censor")
ANIMATIONS = {"highlight": "the spoken word lights up", "karaoke": "karaoke fill", "word": "one word at a time",
              "none": "no animation"}
CANNOT = ("add music or sound effects, change the speed, put stickers, emojis, images or extra text on the video, "
          "add transitions or zooms at chosen moments, blur anything, or change the volume of one part")
DEFAULTS = {"censor": True, "normalize": True}
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_CLOCK = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)$")
_EDGES = re.compile(r"^(\W*)(.*?)(\W*)$", flags=re.S)


@dataclass
class Answer:
    changes: dict[str, Any]
    not_possible: list[str]
    summary: str


@dataclass
class Outcome:
    state: dict[str, Any]                                      # the clip as it will be
    done: list[str] = field(default_factory=list)              # what changed, in words
    not_done: list[str] = field(default_factory=list)          # what was asked for but not made, and why
    fixes: list[tuple[str, str]] = field(default_factory=list)  # word corrections, made once the new cut is saved


# --------------------------------------------------------------------------------------------- helpers
def snapshot(clip: Any) -> dict[str, Any]:
    """What a prompt edit can change on a clip, as plain data (also what undo puts back)."""
    return {name: copy.deepcopy(getattr(clip, name)) for name in EDITABLE}


def restore(clip: Any, state: dict[str, Any]) -> None:
    for name in EDITABLE:
        if name in state:
            setattr(clip, name, copy.deepcopy(state[name]))


def clock(seconds: float) -> str:
    """1:02.5, or 1:02:03.5 past an hour."""
    tenths = int(round(max(0.0, float(seconds)) * 10))
    whole, tenth = divmod(tenths, 10)
    hours, minutes, secs = whole // 3600, whole % 3600 // 60, whole % 60
    return f"{hours}:{minutes:02d}:{secs:02d}.{tenth}" if hours else f"{minutes}:{secs:02d}.{tenth}"


def _number(value: Any) -> float | None:
    """A number of seconds (or a size) from 12.5, "12.5", "12.5 s" or "0:12.5"; None when it is not one."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str):
        text = value.strip().removesuffix("s").strip()
        try:
            number = float(text)
            return number if math.isfinite(number) else None
        except ValueError:
            pass
        match = _CLOCK.match(text)
        if match:
            hours, minutes, secs = match.groups()
            return int(hours or 0) * 3600 + int(minutes) * 60 + float(secs)
    return None


def _flag(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "yes", "on"):
        return True
    if isinstance(value, str) and value.strip().lower() in ("false", "no", "off"):
        return False
    return None


def _shown(value: Any) -> str:
    return " ".join(str(value).split())[:60]


def _snap(t: float, words: Sequence[Any], tie: str) -> float:
    """A cut never falls inside a word: it moves to the word's nearer edge (on a tie, its start or its end, as
    tie says). Claude only sees sentence times, so a time inside a word is a guess at one of its edges."""
    word = next((w for w in words if w.start < t < w.end), None)
    if word is None:
        return t
    to_start, to_end = t - word.start, word.end - t
    if abs(to_start - to_end) < 1e-6:
        return word.start if tie == "start" else word.end
    return word.start if to_start < to_end else word.end


def _snap_start(t: float, words: Sequence[Any]) -> float:
    return _snap(t, words, "start")


def _snap_end(t: float, words: Sequence[Any]) -> float:
    return _snap(t, words, "end")


def _merge(parts: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for a, b in sorted((round(a, 3), round(b, 3)) for a, b in parts if b - a >= 0.1):
        if merged and a <= merged[-1][1] + 0.05:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged[:MAX_CUTS]


def fit_cuts(cuts: Sequence[Sequence[float]], start: float, end: float) -> list[tuple[float, float]]:
    """Parts left out, kept to the cut: those outside it are dropped, those across its ends trimmed to them."""
    return _merge([(max(float(a), start), min(float(b), end)) for a, b in cuts if float(b) > start and float(a) < end])


def _preset(value: Any) -> str | None:
    name = " ".join(str(value).split()).lower()
    for key, label in PRESET_LABELS.items():
        if name in (key, label.lower()):
            return key
    return None


def _layout(value: Any) -> str | None:
    name = " ".join(str(value).split()).lower()
    for key in LAYOUTS:
        if name in (key, LAYOUT_LABELS.get(key, key).lower()):
            return key
    return None


def _font(value: Any, fonts: Sequence[str]) -> str | None:
    name = " ".join(str(value).split()).lower()
    return next((font for font in fonts if font.lower() == name), None)


# ------------------------------------------------------------------------------------------- the changes
def make_changes(state: dict[str, Any], changes: Any, *, duration: float, words: Sequence[Any],
                 fonts: Sequence[str], defaults: dict[str, Any] | None = None,
                 frame: tuple[int, int] | None = None,
                 snap: Callable[[list[int]], list[int]] | None = None) -> Outcome:
    """Check Claude's changes against what ClipForge allows and make them on a copy of the clip's state. Values
    out of range are brought within it; what cannot be made is listed in not_done, with the reason."""
    defaults = {**DEFAULTS, **(defaults or {})}
    out = Outcome(state=copy.deepcopy(state))
    out.state["settings"] = copy.deepcopy(state.get("settings") or {})
    if not isinstance(changes, dict):
        out.not_done.append("Claude's answer had no changes ClipForge could read.")
        return out
    for key in changes:
        if key not in KNOWN:
            out.not_done.append(f"Changing “{_shown(key)}” is not something ClipForge can do yet.")
    _cut(out, changes, duration, words)
    _parts(out, changes, words)
    _pauses(out, changes)
    _subtitles(out, changes, fonts, defaults)
    _word_fixes(out, changes)
    _hook(out, changes)
    _frame(out, changes, frame, snap)
    _sound(out, changes, defaults)
    _title(out, changes)
    _thumbnail(out, changes)
    before, after = state.get("settings") or {}, out.state["settings"]
    timeline = (state["start"], state["end"], before.get("cuts") or [], bool(before.get("remove_silence")))
    if state.get("captions") is not None and timeline != (out.state["start"], out.state["end"],
                                                          after.get("cuts") or [], bool(after.get("remove_silence"))):
        out.state["captions"] = None  # their timings were for the old cut
        out.done.append("The subtitles you edited by hand were reset to fit the new cut")
    return out


def _cut(out: Outcome, changes: dict[str, Any], duration: float, words: Sequence[Any]) -> None:
    if "start" not in changes and "end" not in changes:
        return
    new = out.state
    start, end = new["start"], new["end"]
    for key in ("start", "end"):
        if key not in changes:
            continue
        value = _number(changes[key])
        if value is None:
            out.not_done.append(f"“{_shown(changes[key])}” is not a time, so the cut stays as it was.")
            return
        value = min(max(value, 0.0), duration)
        start, end = (_snap_start(value, words), end) if key == "start" else (start, _snap_end(value, words))
    start, end = round(start, 3), round(end, 3)
    if end - start < MIN_SECONDS:
        out.not_done.append("A clip must be at least 1 second long and end after it starts, so the cut stays as "
                            "it was.")
        return
    if start != new["start"]:
        out.done.append(f"Starts at {clock(start)} instead of {clock(new['start'])}")
    if end != new["end"]:
        out.done.append(f"Ends at {clock(end)} instead of {clock(new['end'])}")
    new["start"], new["end"] = start, end


def _parts(out: Outcome, changes: dict[str, Any], words: Sequence[Any]) -> None:
    """The parts left out of the middle of the clip: the ones asked for, or the ones it had, kept to the cut."""
    st, start, end = out.state["settings"], out.state["start"], out.state["end"]
    had = fit_cuts(st.get("cuts") or [], start, end)
    cuts = had
    if "leave_out" in changes:
        raw = changes["leave_out"]
        if not isinstance(raw, list):
            out.not_done.append("The parts to leave out were not a list of times, so none were left out.")
            raw = None
        if raw is not None:
            asked: list[tuple[float, float]] = []
            for item in raw[:MAX_CUTS * 2]:
                a, b = (_number(item[0]), _number(item[1])) if isinstance(item, (list, tuple)) and len(item) == 2 \
                    else (None, None)
                if a is None or b is None or b <= a:
                    out.not_done.append(f"“{_shown(item)}” is not a part of the clip (from, to), so it was not "
                                        "left out.")
                elif b <= start or a >= end:
                    out.not_done.append(f"{clock(a)}-{clock(b)} is outside the clip, so it was not left out.")
                else:
                    asked.append((_snap_start(max(a, start), words), _snap_end(min(b, end), words)))
            asked = _merge(asked)
            if (end - start) - sum(b - a for a, b in asked) < MIN_SECONDS:
                out.not_done.append("Leaving those parts out would leave less than 1 second of the clip, so "
                                    "nothing more was left out.")
            else:
                cuts = asked
    before = [tuple(c) for c in st.get("cuts") or []]
    if cuts:
        st["cuts"] = [[a, b] for a, b in cuts]
    else:
        st.pop("cuts", None)
    if "leave_out" in changes and not cuts and before:
        out.done.append("Puts back every part that was left out")
        return
    for a, b in cuts:
        if (a, b) not in had:
            out.done.append(f"Leaves out {clock(a)}-{clock(b)} ({b - a:.1f} s)")
    if "leave_out" in changes:
        for a, b in had:
            if (a, b) not in cuts:
                out.done.append(f"Puts back {clock(a)}-{clock(b)}")


def _pauses(out: Outcome, changes: dict[str, Any]) -> None:
    if "tighten_pauses" not in changes:
        return
    value = _flag(changes["tighten_pauses"])
    if value is None:
        out.not_done.append("Tightening pauses can only be switched on or off.")
    elif value != bool(out.state["settings"].get("remove_silence")):
        out.state["settings"]["remove_silence"] = value
        out.done.append("Long pauses tightened" if value else "Long pauses kept")


def _subtitles(out: Outcome, changes: dict[str, Any], fonts: Sequence[str], defaults: dict[str, Any]) -> None:
    raw = changes.get("subtitles")
    if raw is None:
        return
    if not isinstance(raw, dict):
        out.not_done.append("The subtitle changes could not be read, so the subtitles stay as they were.")
        return
    for key in raw:
        if key not in SUBTITLE_KEYS:
            out.not_done.append(f"The subtitles' “{_shown(key)}” cannot be changed in ClipForge yet.")
    st = out.state["settings"]
    had = dict(st.get("captions") or {})
    captions = dict(had)
    before = resolve_style({"censor": defaults["censor"], **captions})

    def put(key: str, value: Any, line: str) -> None:
        captions[key] = value
        out.done.append(line)

    if "style" in raw:  # first: choosing a style starts from it, as in the editor
        preset = _preset(raw["style"])
        if preset is None:
            out.not_done.append(f"There is no subtitle style called “{_shown(raw['style'])}”.")
        elif preset != before.preset:
            captions = {key: captions[key] for key in ("enabled", "censor") if key in captions}
            put("preset", preset, f"Subtitle style: {PRESET_LABELS[preset]}")
    base = resolve_style({"censor": defaults["censor"], **captions})
    if "enabled" in raw:
        value = _flag(raw["enabled"])
        if value is None:
            out.not_done.append("Subtitles can only be switched on or off.")
        elif value != (captions.get("enabled", True) is not False):
            put("enabled", value, "Subtitles on" if value else "Subtitles off")
    if "font" in raw:
        name = _font(raw["font"], fonts)
        if name is None:
            out.not_done.append(f"The font “{_shown(raw['font'])}” is not on this PC, so the subtitles keep "
                                f"{base.font}.")
        elif name != base.font:
            put("font", name, f"Subtitle font: {name}")
    if "size" in raw:
        number = _number(raw["size"])
        if number is None:
            out.not_done.append(f"“{_shown(raw['size'])}” is not a subtitle size.")
        elif (size := int(round(min(220.0, max(30.0, number))))) != base.size:
            put("size", size, f"Subtitle size: {base.size} → {size}")
    if "position" in raw:
        number = _number(raw["position"])
        if number is None:
            out.not_done.append(f"“{_shown(raw['position'])}” is not a height on the screen.")
        elif abs((height := round(min(0.9, max(0.05, number)), 2)) - base.position) > 0.005:
            put("position", height, f"Subtitle height: {round(base.position * 100)}% → {round(height * 100)}% "
                                    "from the top")
    for key, label in (("color", "Subtitle colour"), ("highlight", "Colour of the word being spoken"),
                       ("emphasis", "Colour of important words")):
        if key not in raw:
            continue
        value = raw[key]
        if key == "emphasis" and (value is None or str(value).strip().lower() in ("", "none", "off")):
            if base.emphasis is not None:
                put("emphasis", None, "Important words no longer in colour")
            continue
        if not (isinstance(value, str) and _HEX.match(value.strip())):
            out.not_done.append(f"“{_shown(value)}” is not a colour ClipForge can use (#RRGGBB), so the "
                                f"{label[0].lower() + label[1:]} stays as it was.")
            continue
        value = value.strip().upper()
        if value != (getattr(base, key) or "").upper():
            put(key, value, f"{label}: {value}")
    for key, lines in (("uppercase", ("Subtitles in capitals", "Subtitles in normal letters")),
                       ("box", ("A box behind the subtitles", "No box behind the subtitles")),
                       ("censor", ("Swear words masked", "Swear words shown as said"))):
        if key in raw:
            value = _flag(raw[key])
            if value is None:
                out.not_done.append(f"The subtitles' “{key}” can only be switched on or off.")
            elif value != getattr(base, key):
                put(key, value, lines[0] if value else lines[1])
    if "words_per_line" in raw:
        number = _number(raw["words_per_line"])
        if number is None:
            out.not_done.append(f"“{_shown(raw['words_per_line'])}” is not a number of words.")
        elif (count := int(round(min(8.0, max(1.0, number))))) != base.max_words:
            put("max_words", count, f"Words per line: {base.max_words} → {count}")
    if "animation" in raw:
        value = str(raw["animation"]).strip().lower()
        if value not in ANIMATIONS:
            out.not_done.append(f"There is no subtitle animation called “{_shown(raw['animation'])}”.")
        elif value != base.animation:
            put("animation", value, f"Subtitle animation: {ANIMATIONS[value]}")
    if captions != had:
        st["captions"] = captions


def _word_fixes(out: Outcome, changes: dict[str, Any]) -> None:
    raw = changes.get("fix_words")
    if raw is None:
        return
    if not isinstance(raw, list):
        out.not_done.append("The word corrections could not be read.")
        return
    for item in raw[:20]:
        if not isinstance(item, dict):
            out.not_done.append(f"“{_shown(item)}” is not a word correction ClipForge can read.")
            continue
        wrong, right = (" ".join(str(item.get(key) or "").split()) for key in ("from", "to"))
        if not wrong or not right or len(right) > 60:
            out.not_done.append(f"“{_shown(wrong)}” → “{_shown(right)}” is not a word correction ClipForge can "
                                "make.")
        elif " " in wrong or " " in right:
            out.not_done.append(f"“{wrong}” → “{right}”: the subtitles are corrected one word at a time.")
        else:
            out.fixes.append((wrong, right))


def fix_words(words: Sequence[CaptionWord], fixes: Sequence[tuple[str, str]]
              ) -> tuple[list[CaptionWord], dict[str, int]]:
    """Correct words in the subtitles, whatever their capitals, keeping the punctuation around them."""
    counts = {wrong: 0 for wrong, _ in fixes}
    wanted = [(_EDGES.match(wrong).group(2).lower(), wrong, right) for wrong, right in fixes]
    fixed: list[CaptionWord] = []
    for word in words:
        before, core, after = _EDGES.match(word.text).groups()
        for bare, wrong, right in wanted:
            if bare and core.lower() == bare:
                word = dataclasses.replace(word, text=f"{before}{right}{after}")
                counts[wrong] += 1
                break
        fixed.append(word)
    return fixed, counts


def _hook(out: Outcome, changes: dict[str, Any]) -> None:
    raw = changes.get("hook")
    if raw is None:
        return
    if not isinstance(raw, dict):
        out.not_done.append("The hook title changes could not be read.")
        return
    for key in raw:
        if key not in ("text", "enabled", "seconds"):
            out.not_done.append(f"The hook title's “{_shown(key)}” cannot be changed in ClipForge yet.")
    st, new = out.state["settings"], out.state
    had = dict(st.get("hook") or {})
    hook = dict(had)
    on = hook.get("enabled", True) is not False
    if "text" in raw:
        text = " ".join(str(raw["text"] or "").split()).replace("<", "").replace(">", "")[:300]
        if not text:
            on = False
        elif text != new["hook_text"]:
            new["hook_text"] = text
            out.done.append(f"Hook title: “{text}”")
            on = True  # a new line is meant to be seen
    if "enabled" in raw:
        value = _flag(raw["enabled"])
        if value is None:
            out.not_done.append("The hook title can only be switched on or off.")
        else:
            on = value
    if on != (hook.get("enabled", True) is not False):
        hook["enabled"] = on
        out.done.append("Hook title on" if on else "Hook title off")
    if "seconds" in raw:
        number = _number(raw["seconds"])
        if number is None:
            out.not_done.append(f"“{_shown(raw['seconds'])}” is not a number of seconds.")
        elif (seconds := round(min(15.0, max(1.0, number)) * 2) / 2) != float(hook.get("seconds", 4.0)):
            hook["seconds"] = seconds
            out.done.append(f"Hook title shown for {seconds:g} s")
    if hook != had:
        st["hook"] = hook


def _part(value: Any, frame: tuple[int, int]) -> tuple[list[int] | None, str]:
    """A rectangle of the picture in percent ([x, y, width, height], or with those names), in source pixels and
    kept inside the picture; None and the reason when it is not one."""
    if isinstance(value, dict):
        value = [value.get(key, value.get(long)) for key, long in (("x", "x"), ("y", "y"), ("w", "width"),
                                                                     ("h", "height"))]
    numbers = [_number(v) for v in value] if isinstance(value, (list, tuple)) and len(value) == 4 else []
    if len(numbers) != 4 or any(n is None for n in numbers):
        return None, "is not a rectangle of the picture (x, y, width and height in percent)"
    x, y = (min(max(n, 0.0), 100.0) for n in numbers[:2])
    w, h = min(numbers[2], 100.0 - x), min(numbers[3], 100.0 - y)
    if w < 5 or h < 5:
        return None, "is too small (at least 5% of the picture each way)"
    width, height = frame
    return [round(x * width / 100), round(y * height / 100), round(w * width / 100), round(h * height / 100)], ""


def _where(box: list[int], frame: tuple[int, int]) -> str:
    x, y, w, h = (100 * v / size for v, size in zip(box, (frame[0], frame[1], frame[0], frame[1])))
    return f"{w:.0f}% × {h:.0f}% of the picture, from {x:.0f}% across and {y:.0f}% down"


def _frame(out: Outcome, changes: dict[str, Any], frame: tuple[int, int] | None = None,
           snap: Callable[[list[int]], list[int]] | None = None) -> None:
    raw = changes.get("layout")
    if raw is None:
        return
    if not isinstance(raw, dict):
        out.not_done.append("The layout changes could not be read.")
        return
    for key in raw:
        if key not in ("shape", "layout", "zoom", "subject_x", "top", "bottom"):
            out.not_done.append(f"The layout's “{_shown(key)}” cannot be changed in ClipForge yet.")
    st = out.state["settings"]
    # Parts of the picture for the stacked layouts (top: facecam_box, bottom: gameplay_box). If one is wrong, the
    # layout is left as it is: a split screen half made would hunt for faces again.
    parts: dict[str, list[int]] = {}
    parts_failed = False
    if "top" in raw or "bottom" in raw:
        if frame is None:
            out.not_done.append("Choosing parts of the picture needs a picture of the video, and none could be "
                                "taken this time, so the layout stays as it was.")
            parts_failed = True
        else:
            for key, label in (("top", "top part"), ("bottom", "bottom part")):
                if key in raw:
                    box, why = _part(raw[key], frame)
                    if box is None:
                        out.not_done.append(f"The {label} {why}, so the layout stays as it was.")
                        parts_failed = True
                    else:
                        parts[key] = snap(box) if snap is not None else box
        wanted = _layout(raw["layout"]) if "layout" in raw else None
        target = wanted or (st.get("layout") if st.get("layout") in ("split", "facecam_top") else "split")
        if not parts_failed and target not in ("split", "facecam_top"):
            # parts mean nothing to the other layouts, and left behind they would steer "Automatic" later
            out.not_done.append("Parts of the picture are only used by the stacked layouts (split screen, facecam "
                                "over gameplay), so they were not kept.")
            parts = {}
        has_top = "top" in parts or st.get("facecam_box")
        has_bottom = "bottom" in parts or st.get("gameplay_box")
        if not parts_failed and target == "split" and not (has_top and has_bottom):
            out.not_done.append("A split screen needs both parts, the top and the bottom, so the layout stays as it "
                                "was.")
            parts_failed = True
        if not parts_failed:
            if target != st.get("layout", "auto"):
                st["layout"] = target
                out.done.append(f"Layout: {LAYOUT_LABELS[target]}")
            for key, setting, label in (("top", "facecam_box", "Top part"), ("bottom", "gameplay_box", "Bottom part")):
                if key in parts and parts[key] != st.get(setting):
                    st[setting] = parts[key]
                    out.done.append(f"{label}: {_where(parts[key], frame)}")
    if parts_failed:
        raw = {key: value for key, value in raw.items() if key != "layout"}
    if "shape" in raw:
        shape = str(raw["shape"]).strip()
        if shape not in ASPECTS:
            out.not_done.append(f"ClipForge has no {_shown(shape)} shape (it has {', '.join(ASPECTS)}).")
        elif shape != st.get("aspect", "9:16"):
            st["aspect"] = shape
            out.done.append(f"Shape: {shape}")
    if "layout" in raw and not parts:
        layout = _layout(raw["layout"])
        if layout is None:
            out.not_done.append(f"There is no layout called “{_shown(raw['layout'])}”.")
        elif layout != st.get("layout", "auto"):
            st["layout"] = layout
            out.done.append(f"Layout: {LAYOUT_LABELS[layout]}")
    if "zoom" in raw:
        number = _number(raw["zoom"])
        if number is None:
            out.not_done.append(f"“{_shown(raw['zoom'])}” is not a zoom.")
        elif (zoom := round(min(2.5, max(1.0, number)), 2)) != float(st.get("zoom") or 1.0):
            out.done.append(f"Zoom: {float(st.get('zoom') or 1.0):g}× → {zoom:g}×")
            st["zoom"] = zoom
    if "subject_x" in raw:
        value = raw["subject_x"]
        if value is None or str(value).strip().lower() in ("auto", "automatic", "null"):
            if st.get("subject_x") is not None:
                st["subject_x"] = None
                out.done.append("The person in frame is found automatically")
        elif (number := _number(value)) is None:
            out.not_done.append(f"“{_shown(value)}” is not a place in the picture.")
        elif (x := round(min(1.0, max(0.0, number)), 2)) != st.get("subject_x"):
            st["subject_x"] = x
            if st.get("layout", "auto") == "auto":
                st["layout"] = "fill"  # as in the editor: a chosen subject means cropping to it
            out.done.append(f"Keeps {round(x * 100)}% from the left in frame")


def _sound(out: Outcome, changes: dict[str, Any], defaults: dict[str, Any]) -> None:
    raw = changes.get("sound")
    if raw is None:
        return
    if not isinstance(raw, dict):
        out.not_done.append("The sound changes could not be read.")
        return
    st = out.state["settings"]
    audio = dict(st.get("audio") or {})
    for key in raw:
        if key not in ("level_volume", "reduce_noise"):
            out.not_done.append(f"The sound's “{_shown(key)}” cannot be changed in ClipForge yet.")
    for key, setting, default, lines in (
            ("level_volume", "normalize", defaults["normalize"], ("Volume levelled out", "Volume left as recorded")),
            ("reduce_noise", "denoise", False, ("Background noise reduced", "Background noise left in"))):
        if key in raw:
            value = _flag(raw[key])
            if value is None:
                out.not_done.append("That sound setting can only be switched on or off.")
            elif value != bool(audio.get(setting, default)):
                audio[setting] = value
                out.done.append(lines[0] if value else lines[1])
    if audio != (st.get("audio") or {}):
        st["audio"] = audio


def _title(out: Outcome, changes: dict[str, Any]) -> None:
    if "title" not in changes:
        return
    title = " ".join(str(changes["title"] or "").split()).replace("<", "").replace(">", "")[:100]
    if not title:
        out.not_done.append("A clip needs a title, so it keeps the one it has.")
    elif title != out.state["title"]:
        out.state["title"] = title
        out.done.append(f"Title: “{title}”")


def _thumbnail(out: Outcome, changes: dict[str, Any]) -> None:
    raw = changes.get("thumbnail")
    if raw is None:
        return
    if not isinstance(raw, dict):
        out.not_done.append("The thumbnail changes could not be read.")
        return
    for key in raw:
        if key not in ("text", "word", "moment"):
            out.not_done.append(f"The thumbnail's “{_shown(key)}” cannot be changed in ClipForge yet.")
    new = out.state
    if "text" in raw or "word" in raw:
        text = str(raw["text"] or "") if "text" in raw else (new["cover_text"] or new["title"])
        words, coloured = clean_words(text, str(raw.get("word") or ""))
        if words and not can_draw(words):
            out.not_done.append("The fonts on this PC cannot draw those thumbnail words, so they stay as they were.")
        elif (words, coloured) != (new["cover_text"], new["cover_word"]):
            new["cover_text"], new["cover_word"] = words, coloured
            out.done.append(f"Thumbnail words: “{words}”" + (f", {coloured} in colour" if coloured else "")
                            if words else "Thumbnail words from the title")
    if "moment" in raw:
        value = raw["moment"]
        if value is None or str(value).strip().lower() in ("auto", "automatic", "null"):
            if new["cover_time"] is not None:
                new["cover_time"] = None
                out.done.append("Thumbnail picture: the best moment is picked")
        elif (moment := _number(value)) is None or not new["start"] <= moment <= new["end"]:
            out.not_done.append(f"“{_shown(value)}” is not a moment inside the clip, so the thumbnail keeps its "
                                "picture.")
        elif round(moment, 3) != new["cover_time"]:
            new["cover_time"] = round(moment, 3)
            out.done.append(f"Thumbnail picture from {clock(moment)}")


# ------------------------------------------------------------------------------- what Claude looks at
SHEET_FRAMES = 8


def typical_frame(frames: Sequence[np.ndarray]) -> int:
    """The frame most like the others: in a recorded call the layout can change for a while (one camera shown
    large), and the frame Claude measures on should show how the clip usually looks."""
    small = [np.asarray(frame, dtype=np.float32) for frame in frames]
    totals = [sum(float(np.mean(np.abs(a - b))) for b in small) for a in small]
    return int(np.argmin(totals))


def frame_sheet(frames: Sequence[Image.Image], labels: Sequence[str], columns: int = 4) -> bytes:
    """Small frames side by side, each with its time in the clip, as one JPEG."""
    width, height = frames[0].size
    rows = math.ceil(len(frames) / columns)
    sheet = Image.new("RGB", (columns * width, rows * height), (0, 0, 0))
    try:
        font = ImageFont.load_default(size=max(14, height // 9))
    except TypeError:  # an older Pillow: its one small font
        font = ImageFont.load_default()
    for index, (frame, label) in enumerate(zip(frames, labels)):
        x, y = (index % columns) * width, (index // columns) * height
        sheet.paste(frame.convert("RGB").resize((width, height)), (x, y))
        draw = ImageDraw.Draw(sheet)
        box = draw.textbbox((x + 6, y + 4), label, font=font)
        draw.rectangle((box[0] - 4, box[1] - 2, box[2] + 4, box[3] + 2), fill=(0, 0, 0))
        draw.text((x + 6, y + 4), label, fill=(255, 230, 0), font=font)
    out = io.BytesIO()
    sheet.save(out, format="JPEG", quality=80)
    return out.getvalue()


@dataclass
class Pictures:
    jpegs: list[bytes] = field(default_factory=list)       # the typical frame, large; the frames across the clip
    at: float | None = None                                 # the source time of the typical frame
    labels: list[str] = field(default_factory=list)         # the times in the clip of the frames across it
    grey: list[np.ndarray] = field(default_factory=list)    # those frames in grey, to find edges in


def pictures_of(source: Path, start: float, end: float) -> Pictures:
    """What Claude looks at: the frame most like the rest of the clip, large (it measures on it), and frames
    across the clip with their times (to see whether the picture changes). Nothing when they cannot be taken."""
    length = max(0.0, end - start)
    times = [start + length * (i + 0.5) / SHEET_FRAMES for i in range(SHEET_FRAMES)]
    try:
        with tempfile.TemporaryDirectory(prefix="clipforge_frames_") as folder:
            frames = []
            for i, at in enumerate(times):
                path = ffmpeg.grab_frame(source, at, Path(folder) / f"small_{i}.jpg", width=640)
                with Image.open(path) as image:
                    frames.append(image.convert("RGB"))
            grey = [np.asarray(frame.convert("L")) for frame in frames]
            typical = typical_frame([np.asarray(frame.convert("L").resize((64, 36))) for frame in frames])
            large = ffmpeg.grab_frame(source, times[typical], Path(folder) / "frame.jpg", width=1280).read_bytes()
    except (ffmpeg.FFmpegError, OSError):
        return Pictures()
    labels = [clock(at - start) for at in times]
    small = [frame.resize((320, max(2, round(frame.height * 320 / frame.width)))) for frame in frames]
    return Pictures([large, frame_sheet(small, labels)], round(times[typical], 3), labels, grey)


def snap_part(frames: Sequence[np.ndarray], box: Sequence[float], source: tuple[int, int],
              reach: float = 0.06) -> list[int]:
    """A part drawn round a camera (in source pixels), moved onto the camera's own edges: in each frame, the
    strongest straight line near each side, along most of that side. The camera can move a little during the
    clip (seen for real: Discord's tiles moved 18 px when a bar at its top came and went), and one crop serves
    the whole clip, so each side goes to where it is inside the camera in every frame that shows it. A side with
    no clear edge near it in enough frames stays where it was drawn. Seen for real too: Claude's rectangle began
    about 15 px above the camera, which put a black line into the clip."""
    stack = np.stack([np.asarray(frame, dtype=np.float32) for frame in frames])
    count, height, width = stack.shape
    sx, sy = source[0] / width, source[1] / height
    x, y, w, h = box[0] / sx, box[1] / sy, box[2] / sx, box[3] / sy
    rows_along = slice(int(max(0, y + 0.15 * h)), int(min(height, y + 0.85 * h)))
    columns_along = slice(int(max(0, x + 0.15 * w)), int(min(width, x + 0.85 * w)))
    if rows_along.stop - rows_along.start < 2 or columns_along.stop - columns_along.start < 2:
        return [int(round(v)) for v in box]
    # per frame: how strong a vertical line is at each column, and a horizontal one at each row (index i: between
    # i and i + 1), averaged along the part's side so that only lines as long as the side count
    across = np.abs(np.diff(stack[:, rows_along, :], axis=2)).mean(axis=1)
    down = np.abs(np.diff(stack[:, :, columns_along], axis=1)).mean(axis=2)

    def bands(index: int, at: int, side: str) -> tuple[np.ndarray, np.ndarray]:
        """The few lines just outside and just inside a side placed at `at`, along that side."""
        frame = stack[index]
        if side in ("top", "bottom"):
            above, below = frame[max(0, at - 3):at, columns_along], frame[at:at + 3, columns_along]
            return (above, below) if side == "top" else (below, above)
        before, after = frame[rows_along, max(0, at - 3):at], frame[rows_along, at:at + 3]
        return (before, after) if side == "left" else (after, before)

    def edge(profiles: np.ndarray, at: float, size: int, side: str) -> int | None:
        low, high = int(max(0, round(at - reach * size))), int(min(profiles.shape[1] - 1, round(at + reach * size)))
        if high - low < 4:
            return None
        found = []
        for index, profile in enumerate(profiles):
            values = profile[low:high + 1]
            floor = 4.0 * (float(np.median(values)) + 1.0)
            for offset in np.argsort(values)[::-1]:  # the strongest lines first
                if values[offset] <= 25 or values[offset] <= floor:
                    break
                position = low + int(offset) + 1  # the edge lies between this line and the next
                outside, inside = bands(index, position, side)
                # a camera's edge has the call's empty background outside it: dark and even. A line inside the
                # picture (a door frame) has picture on both sides.
                if (outside.size and inside.size and float(outside.mean()) < 40 and float(outside.std()) < 10
                        and float(inside.mean()) > float(outside.mean()) + 20):
                    found.append(position)
                    break
        if len(found) < max(2, count // 3):  # not a steady edge
            return None
        return max(found) if side in ("left", "top") else min(found)  # inside the camera in every frame

    # a side drawn at the picture's own edge stays there: that edge is the camera's edge too
    left = None if x <= 0.01 * width else edge(across, x, width, "left")
    right = None if x + w >= 0.99 * width else edge(across, x + w, width, "right")
    top = None if y <= 0.01 * height else edge(down, y, height, "top")
    bottom = None if y + h >= 0.99 * height else edge(down, y + h, height, "bottom")
    x0 = left if left is not None else x
    x1 = right if right is not None else x + w
    y0 = top if top is not None else y
    y1 = bottom if bottom is not None else y + h
    if x1 - x0 < 0.75 * w or y1 - y0 < 0.75 * h:  # not the camera's edges after all: keep it as drawn
        return [int(round(v)) for v in box]
    return [int(round(x0 * sx)), int(round(y0 * sy)), int(round((x1 - x0) * sx)), int(round((y1 - y0) * sy))]


# ------------------------------------------------------------------------------------------------ Claude
def _clip_time(t: float, segments: Sequence[Sequence[float]]) -> float:
    played = 0.0
    for a, b in segments:
        if t <= b:
            return played + max(0.0, t - a)
        played += b - a
    return played


def _transcript(state: dict[str, Any], sentences: Sequence[tuple[float, float, str]],
                segments: Sequence[Sequence[float]]) -> str:
    start, end = state["start"], state["end"]
    lo, hi = start - CONTEXT_SECONDS, end + CONTEXT_SECONDS
    inside: list[str] = []
    before: list[str] = []
    after: list[str] = []
    for s, e, text in sentences:
        if e < lo or s > hi:
            continue
        text = " ".join(str(text).split())
        played = [(max(s, a), min(e, b)) for a, b in segments if min(e, b) > max(s, a)]
        if played:
            line = (f"> [source {s:.1f}-{e:.1f} s | clip {clock(_clip_time(played[0][0], segments))}-"
                    f"{clock(_clip_time(played[-1][1], segments))}] {text}")
            inside.append(line)
        elif e <= start + 0.01:
            before.append(f"  [source {s:.1f}-{e:.1f} s] {text}")
        elif s >= end - 0.01:
            after.append(f"  [source {s:.1f}-{e:.1f} s] {text}")
        else:
            inside.append(f"  [source {s:.1f}-{e:.1f} s] (left out of the clip) {text}")
    budget = TRANSCRIPT_BUDGET
    kept_inside: list[str] = []
    for line in inside:
        if budget - len(line) - 1 < 0:
            kept_inside.append("  ... (the rest of the clip is not shown)")
            budget = 0
            break
        kept_inside.append(line)
        budget -= len(line) + 1
    kept_before: list[str] = []
    kept_after: list[str] = []
    for i in range(max(len(before), len(after))):  # the nearest context first
        for source, kept in ((before, kept_before), (after, kept_after)):
            if i < len(source):
                line = source[-1 - i] if source is before else source[i]
                if budget - len(line) - 1 >= 0:
                    kept.append(line)
                    budget -= len(line) + 1
    return "\n".join([*reversed(kept_before), *kept_inside, *kept_after]) or "(nothing is said around the clip)"


def build_prompt(instruction: str, state: dict[str, Any], *, video_title: str, creator: str, duration: float,
                 sentences: Sequence[tuple[float, float, str]], segments: Sequence[Sequence[float]],
                 fonts: Sequence[str], defaults: dict[str, Any] | None = None,
                 frame: tuple[int, int] | None = None, picture_at: float | None = None,
                 sheet: Sequence[str] = ()) -> str:
    """The request to Claude: what the user wants, the clip as it is, what can be changed, and what is said."""
    defaults = {**DEFAULTS, **(defaults or {})}
    st = state.get("settings") or {}
    captions = st.get("captions") or {}
    style = resolve_style({"censor": defaults["censor"], **captions})
    hook, audio, cuts = st.get("hook") or {}, st.get("audio") or {}, st.get("cuts") or []
    layout = st.get("layout") if st.get("layout") in LAYOUTS else "auto"
    plays = sum(b - a for a, b in segments)
    yes = lambda value: "yes" if value else "no"  # noqa: E731
    subject = st.get("subject_x")
    now = "\n".join([
        f'- Source video: "{video_title}"' + (f" by {creator}" if creator else "") + f", {duration:.1f} s long.",
        f"- Cut: from {state['start']:.1f} s to {state['end']:.1f} s of the source video; the clip plays for "
        f"{plays:.1f} s.",
        "- Parts left out: " + (", ".join(f"{a:.1f}-{b:.1f} s" for a, b in cuts) or "none") + ".",
        f"- Long pauses tightened: {yes(st.get('remove_silence'))}.",
        f'- Title: "{state["title"]}".',
        f'- Hook title (shown at the start): "{state["hook_text"]}", '
        + ("on" if hook.get("enabled", True) is not False else "off") + f", shown for {hook.get('seconds', 4.0):g} s.",
        f"- Subtitles: {'on' if captions.get('enabled', True) is not False else 'off'}; style {style.preset} "
        f"({PRESET_LABELS.get(style.preset, style.preset)}); font {style.font}; size {style.size}; height "
        f"{style.position:.2f}; colour {style.color}; word being spoken {style.highlight}; important words "
        f"{style.emphasis or 'not coloured'}; capitals {yes(style.uppercase)}; words per line {style.max_words}; "
        f"animation {style.animation}; box {yes(style.box)}; swear words masked {yes(style.censor)}.",
        f"- Layout: shape {st.get('aspect', '9:16')}; layout {layout} ({LAYOUT_LABELS[layout]}); zoom "
        f"{float(st.get('zoom') or 1.0):g}; person in frame "
        + ("found automatically" if subject is None else f"at {subject:.2f} from the left") + ".",
        f"- Sound: volume levelled {yes(audio.get('normalize', defaults['normalize']))}; background noise reduced "
        f"{yes(audio.get('denoise', False))}.",
        f'- Thumbnail words: "{state.get("cover_text") or "(made from the title)"}"; picture from '
        + (f"{state['cover_time']:.1f} s" if state.get("cover_time") is not None else "the best moment") + ".",
    ])
    styles = ", ".join(f"{key} ({label})" for key, label in PRESET_LABELS.items() if key in PRESETS)
    if frame is not None:
        seen = (f" at {clock(picture_at - state['start'])} of the clip" if picture_at is not None else "")
        looking = (f"\nTwo pictures are attached. The first is one frame{seen}: the whole frame of the source video "
                   f"({frame[0]}x{frame[1]}) before it is framed for the clip, picked as the one most like the rest "
                   "of the clip. Give rectangles in percent of this first picture. The second shows "
                   f"{len(sheet) or SHEET_FRAMES} small frames across the clip, left to right and top to bottom, each "
                   f"with its time in the clip ({', '.join(sheet)}), to show whether the picture changes. If what the "
                   "user wants is only visible at some times, or a camera moves or is missing at times, choose for "
                   "the usual picture and say in not_possible when it differs: the layout cannot change during a "
                   "clip.\n")
        parts = ("""
  With the picture you can also choose what the stacked layouts show: "top" and "bottom", each the rectangle
  of the picture to show there, as [x, y, width, height] in percent of the picture (0 to 100). With "layout":
  "split" each part gets half of the height: use it for two people or two cameras, and put the one the user
  wants above in "top". With "facecam_top", "top" is the facecam and "bottom" the gameplay. Draw each
  rectangle round the whole camera or area, inside its border.""")
    else:
        looking = "\nNo picture of the video is attached this time, so parts of the picture cannot be chosen.\n"
        parts = ""
    layouts = ", ".join(f"{key} ({LAYOUT_LABELS[key]})" for key in LAYOUTS)
    return f"""You edit a short video clip that ClipForge, a clipping app, cut from a longer video. The user describes
in their own words what they want changed. Turn that into changes ClipForge can make, and only those.

What the user wants:
\"\"\"
{instruction.strip()[:INSTRUCTION_MAX]}
\"\"\"
{looking}
The clip now:
{now}

What you can change (leave out everything that should stay as it is):
- "start", "end": a new cut, in seconds of the source video (0 to {duration:.1f}). The clip must stay at least 1
  second long. Start and end on what is said: use the times in the transcript.
- "leave_out": the complete list of parts to cut out of the clip, each [from, to] in seconds of the source video,
  inside the cut. It replaces the parts left out now; [] puts them all back.
- "tighten_pauses": true or false: cut long pauses down to a short breath.
- "subtitles": an object with any of: "enabled" (true or false); "style" (one of: {styles}); "font" (one
  of the fonts below); "size" (30 to 220); "position" (the height of the subtitles, 0.05 = top to 0.9 = bottom);
  "color", "highlight" (the word being spoken), "emphasis" (important words; null for none): colours as #RRGGBB;
  "uppercase" (true or false); "words_per_line" (1 to 8); "animation" (highlight, karaoke, word or none); "box"
  (true or false: a solid box behind the text); "censor" (true or false: mask swear words). Choosing a style starts
  from that style's own font, size and colours.
- "fix_words": [{{"from": "the word as the subtitles show it", "to": "how it should be written"}}]: one word each,
  for example a name spelled wrong.
- "hook": an object with any of: "text" (the line shown at the start; "" switches it off), "enabled" (true or
  false), "seconds" (how long it shows, 1 to 15).
- "layout": an object with any of: "shape" ({", ".join(ASPECTS)}); "layout" ({layouts}); "zoom" (1 to 2.5, for the
  cropped layouts); "subject_x" (0 to 1: where the person to keep in frame is, from the left; null: found
  automatically).{parts}
- "sound": an object with any of: "level_volume" (true or false), "reduce_noise" (true or false).
- "title": the clip's title.
- "thumbnail": an object with any of: "text" (2 to 6 words on the thumbnail), "word" (one of those words, shown in
  colour), "moment" (the source time, inside the cut, of its picture; null: the best moment is picked).

Fonts on this PC: {", ".join(fonts) or "none found"}

Times the user gives are times in the clip as they watch it (0:00 is its first frame) unless they clearly mean the
source video; lines inside the clip show both. Answer in seconds of the source video.
When the user gives the exact words for a title, hook or thumbnail, use them as given. When you write the words,
keep them true to what happens in the clip: no invented quotes, events or claims.
ClipForge cannot {CANNOT}. When the user asks for that, or for anything not in the list above, say so in
"not_possible", one short sentence each. Never pretend something was done.

Transcript, in seconds of the source video (lines starting with > are in the clip now):
{_transcript(state, sentences, segments)}

Answer with JSON only, in this shape:
{{"changes": {{...only what changes...}}, "not_possible": ["..."], "summary": "one short sentence on what you changed"}}"""


def parse_answer(printed: str) -> Answer:
    """Claude's answer, read from what the command-line tool printed. An answer that cannot be read is an error,
    never a guess."""
    try:
        envelope = json.loads(printed)
    except ValueError:
        envelope = {"result": printed}
    if isinstance(envelope, dict) and envelope.get("is_error"):
        raise ClipForgeError(f"Claude could not make the change: {str(envelope.get('result') or '')[:200]}")
    text = str(envelope.get("result") or "") if isinstance(envelope, dict) else ""
    # the last block first: a correction comes after its draft
    data = next((block for block in json_answers(text) if {"changes", "not_possible", "summary"} & set(block)),
                None)
    if data is None or not isinstance(data.get("changes") or {}, dict):
        raise ClipForgeError("Claude's answer could not be read, so nothing was changed. Try again.")
    reasons = data.get("not_possible") or []
    reasons = [reasons] if isinstance(reasons, str) else reasons if isinstance(reasons, list) else []
    return Answer(changes=data.get("changes") or {},
                  not_possible=[" ".join(str(r).split())[:300] for r in reasons if str(r).strip()][:10],
                  summary=" ".join(str(data.get("summary") or "").split())[:400])


def ask(prompt: str, model: str, pictures: Sequence[bytes] = ()) -> Answer:
    """One request to Claude through the Claude Code tool (your plan, no tools of its own), with pictures of
    the video when there are any."""
    try:
        printed = claude_review.run_claude(prompt, model=model, timeout=ASK_TIMEOUT, pictures=pictures)
    except claude_review.ClaudeUnavailable as exc:
        raise DependencyMissing(exc.message.split(",")[0].rstrip(".") + ".", hint=exc.hint) from exc
    return parse_answer(printed)
