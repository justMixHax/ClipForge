"""Captions: from timed words to burned-in subtitles (ASS) and caption files (SRT, VTT).

The words are never rewritten. Styling may upper-case them, colour the word being spoken, emphasise a few
important words, or mask swearing on screen, but the SRT/VTT files and the transcript stay verbatim.
Every caption is editable: the editor works on the same word list this module renders.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, fields, replace
from typing import Any

from ..transcription.sentences import Word
from ..transcription.whisper_engine import is_noise_token
from .fonts import FontInfo, pick_font, text_width

# Vertical centre of the captions must stay in this band (fractions of the frame height): clear of the
# platform's top bar and of the description, buttons and progress bar at the bottom.
SAFE_TOP, SAFE_BOTTOM = 0.10, 0.80
MAX_TEXT_WIDTH = 0.90          # share of the frame width a caption line may use
MIN_SHRINK = 0.55              # a line is never shrunk below this share of the style's size
GROUP_PAUSE = 0.45             # a pause this long starts a new caption line
LINE_HOLD = 0.20               # keep a finished line on screen briefly, unless the next one starts
POP_SCALE = 108                # % size of the word being spoken in highlight styles

SWEARS = re.compile(
    r"\b(f+u+c*k+\w*|motherf\w*|shit\w*|bullshit|bitch\w*|ass(hole)?s?|dick(head)?s?|pussy|cock(sucker)?s?|"
    r"cunt\w*|bastard\w*|retard\w*|slut\w*|whore\w*|porn\w*)\b", re.I)
EMPHASIS_WORDS = {
    "never", "always", "insane", "crazy", "craziest", "unbelievable", "incredible", "impossible", "worst", "best",
    "literally", "everything", "nothing", "nobody", "everyone", "million", "billion", "thousand", "hundred", "dead",
    "died", "killed", "won", "lost", "free", "secret", "huge", "massive", "perfect", "terrible", "wrong", "hate",
    "love", "dumbest", "funniest", "headshot", "clutch", "gone", "wow", "illegal", "banned", "broke", "rich",
    "fired", "cheating", "hacker", "record", "first", "last", "only", "biggest", "fastest", "hardest", "scariest",
}


@dataclass(slots=True)
class CaptionWord:
    text: str
    start: float           # seconds from the start of the clip
    end: float
    emph: bool = False     # drawn in the emphasis colour
    locked: bool = False   # emphasis was set by hand: automatic marking leaves it alone
    hidden: bool = False   # kept in the transcript but not shown
    prob: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CaptionWord:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass(slots=True)
class CaptionStyle:
    preset: str = "opus"
    font: str = "Arial Black"
    size: int = 92                  # at 1080 px wide; scales with the output width
    color: str = "#FFFFFF"
    highlight: str = "#FFE600"      # the word being spoken
    emphasis: str | None = "#3DFF8B"  # important words; None switches emphasis colouring off
    outline_color: str = "#000000"
    outline: float = 7.0
    shadow: float = 2.0
    bold: bool = True
    uppercase: bool = True
    max_words: int = 3
    max_chars: int = 18
    position: float = 0.70          # vertical centre, 0 = top, 1 = bottom (kept inside the safe area)
    animation: str = "highlight"    # highlight | karaoke | word | none
    pop: bool = True                # the spoken word grows slightly
    box: bool = False               # solid box behind the text instead of an outline
    box_color: str = "#000000"
    box_opacity: float = 0.75
    spacing: float = 0.0
    censor: bool = True             # mask swear words in the burned-in text
    hook_size: int = 56
    hook_color: str = "#111111"
    hook_box: str = "#FFFFFF"


PRESETS: dict[str, CaptionStyle] = {
    "opus": CaptionStyle(),
    "karaoke": CaptionStyle(preset="karaoke", size=88, max_words=4, max_chars=22, animation="karaoke",
                            highlight="#39FF14", emphasis=None, pop=False),
    "minimal": CaptionStyle(preset="minimal", font="Segoe UI", size=62, uppercase=False, max_words=6, max_chars=32,
                            outline=3.0, shadow=1.0, animation="none", emphasis=None, pop=False, position=0.76),
    "gaming": CaptionStyle(preset="gaming", font="Impact", size=104, outline=8.0, shadow=3.0, highlight="#39FF14",
                           emphasis="#FF3B3B", max_chars=16),
    "bold": CaptionStyle(preset="bold", size=84, box=True, outline=0.0, shadow=0.0, highlight="#FFD60A",
                         emphasis="#FFD60A"),
    "word": CaptionStyle(preset="word", font="Impact", size=124, max_words=1, max_chars=40, animation="word",
                         highlight="#FFFFFF", emphasis="#FFE600", outline=8.0),
}
PRESET_LABELS = {"opus": "Opus style", "karaoke": "Karaoke fill", "minimal": "Clean minimal",
                 "gaming": "Gaming", "bold": "Bold creator", "word": "Word by word"}
_LIMITS = {"size": (30, 220), "max_words": (1, 8), "max_chars": (6, 60), "position": (0.0, 1.0),
           "outline": (0.0, 20.0), "shadow": (0.0, 12.0), "box_opacity": (0.0, 1.0), "spacing": (0.0, 20.0),
           "hook_size": (24, 140)}
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def resolve_style(overrides: dict[str, Any] | None) -> CaptionStyle:
    """A preset with the user's adjustments applied. Unknown keys and out-of-range values are ignored."""
    overrides = overrides or {}
    style = replace(PRESETS.get(str(overrides.get("preset", "opus")), PRESETS["opus"]))
    for f in fields(CaptionStyle):
        if f.name == "preset" or f.name not in overrides:
            continue
        value, current = overrides[f.name], getattr(style, f.name)
        if f.name in ("color", "highlight", "outline_color", "box_color", "hook_color", "hook_box"):
            if isinstance(value, str) and _HEX.match(value):
                setattr(style, f.name, value.upper())
        elif f.name == "emphasis":
            if value is None or (isinstance(value, str) and _HEX.match(value)):
                style.emphasis = value.upper() if value else None
        elif f.name == "animation":
            if value in ("highlight", "karaoke", "word", "none"):
                style.animation = value
        elif f.name == "font":
            if isinstance(value, str) and 0 < len(value) <= 80 and re.fullmatch(r"[\w .\-]+", value):
                style.font = value
        elif isinstance(current, bool):
            setattr(style, f.name, bool(value))
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            low, high = _LIMITS.get(f.name, (float("-inf"), float("inf")))
            number = min(high, max(low, float(value)))
            setattr(style, f.name, int(round(number)) if isinstance(current, int) else number)
    return style


# -------------------------------------------------------------------------------------------- word prep
def words_for_clip(source_words: list[Word], segments: list[tuple[float, float]]) -> list[CaptionWord]:
    """Move transcript words (source time) into clip time. `segments` are the source ranges the clip is
    made of, in order; anything between them was cut out, so later words shift earlier accordingly."""
    out: list[CaptionWord] = []
    offset = 0.0
    for seg_start, seg_end in segments:
        for word in source_words:
            text = word.text.strip()
            if not text or is_noise_token(text):
                continue
            middle = (word.start + word.end) / 2
            if seg_start <= middle < seg_end:
                start = offset + max(0.0, word.start - seg_start)
                end = offset + min(seg_end - seg_start, word.end - seg_start)
                out.append(CaptionWord(text=text, start=round(start, 3), end=round(max(end, start + 0.04), 3),
                                       prob=word.prob))
        offset += seg_end - seg_start
    return out


def censor(text: str) -> str:
    return SWEARS.sub(lambda m: m.group(0)[0] + "*" * (len(m.group(0)) - 2) + m.group(0)[-1], text)


def mark_emphasis(words: list[CaptionWord]) -> None:
    """Flag a few important words (numbers, strong words). At most a quarter of the words, and never two
    close together, so emphasis keeps its meaning. Words the user set by hand are not touched."""
    budget = max(1, len(words) // 4)
    used = sum(1 for w in words if w.locked and w.emph)
    last = -10
    for index, word in enumerate(words):
        if word.locked:
            if word.emph:
                last = index
            continue
        core = word.text.lower().strip(".,!?;:\"'()[]…-")
        strong = any(ch.isdigit() for ch in core) or core in EMPHASIS_WORDS
        word.emph = bool(strong and used < budget and index - last >= 3)
        if word.emph:
            used += 1
            last = index


def group_words(words: list[CaptionWord], style: CaptionStyle) -> list[list[CaptionWord]]:
    """Split the visible words into caption lines."""
    groups: list[list[CaptionWord]] = []
    current: list[CaptionWord] = []
    for word in words:
        if word.hidden or not word.text.strip():
            continue
        if current:
            previous = current[-1]
            line = " ".join(w.text for w in current)
            ended = previous.text.rstrip("\"')]”’")[-1:] in ".?!…"
            comma = previous.text.rstrip("\"')]”’")[-1:] in ",;:" and len(current) >= 2
            if (len(current) >= style.max_words or ended or comma or word.start - previous.end > GROUP_PAUSE
                    or len(line) + 1 + len(word.text) > style.max_chars):
                groups.append(current)
                current = []
        current.append(word)
    if current:
        groups.append(current)
    return groups


# ------------------------------------------------------------------------------------------- ASS output
def ass_color(hex_color: str, opacity: float = 1.0) -> str:
    """#RRGGBB -> &HAABBGGRR (ASS stores blue first, and alpha as transparency)."""
    r, g, b = hex_color[1:3], hex_color[3:5], hex_color[5:7]
    alpha = int(round((1.0 - min(1.0, max(0.0, opacity))) * 255))
    return f"&H{alpha:02X}{b}{g}{r}".upper().replace("&H", "&H", 1)


def ass_time(seconds: float) -> str:
    cs = int(round(max(0.0, seconds) * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _inline(color: str) -> str:
    """Colour for an inline override tag: &HBBGGRR& (no alpha)."""
    return f"&H{color[5:7]}{color[3:5]}{color[1:3]}&".upper()


def _display(text: str, style: CaptionStyle) -> str:
    text = text.replace("{", "(").replace("}", ")").replace("\\", "/")  # no way to smuggle in override codes
    if style.censor:
        text = censor(text)
    if not style.uppercase:
        return text
    # Capital-letter styles show a few words at a time, so a trailing full stop or comma is just clutter.
    # Question and exclamation marks carry meaning and stay.
    return (text.rstrip(".,;:") or text).upper()


def _fit(groups: list[list[CaptionWord]], style: CaptionStyle, font: FontInfo | None, size: float, max_width: float,
         report: dict[str, Any]) -> list[tuple[list[CaptionWord], float]]:
    """Give every line a font size at which it fits the frame; split lines that would need to shrink too far."""
    fitted: list[tuple[list[CaptionWord], float]] = []
    pop = POP_SCALE / 100 if (style.pop and style.animation == "highlight") else 1.0
    queue = list(groups)
    while queue:
        group = queue.pop(0)
        text = " ".join(_display(w.text, style) for w in group)
        width = text_width(text, font, size, style.spacing) * (1 + (pop - 1) / max(1, len(group)))
        if width <= max_width:
            fitted.append((group, size))
            report["max_width"] = max(report["max_width"], round(width, 1))
            continue
        factor = max_width / width
        if factor >= MIN_SHRINK or len(group) == 1:
            new_size = max(8.0, float(int(size * factor)))
            fitted.append((group, new_size))
            report["shrunk"] += 1
            report["max_width"] = max(report["max_width"], round(width * new_size / size, 1))
            if factor < MIN_SHRINK:
                report["overflow"].append(text)
            continue
        half = (len(group) + 1) // 2
        queue[0:0] = [group[:half], group[half:]]
    return fitted


def _wrap_hook(text: str, font: FontInfo | None, size: float, max_width: float) -> str:
    lines: list[str] = []
    current = ""
    for token in text.split():
        candidate = f"{current} {token}".strip()
        if not current or text_width(candidate, font, size) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = token
    if current:
        lines.append(current)
    return "\\N".join(lines[:3])


def build_ass(words: list[CaptionWord], style: CaptionStyle, out_w: int, out_h: int, *, hook: str | None = None,
              hook_seconds: float = 4.0, clip_seconds: float | None = None, position_y: float | None = None,
              hook_y: float | None = None) -> tuple[str, dict[str, Any]]:
    """The subtitle script plus a report (font used, lines shrunk, lines that still overflow)."""
    scale = out_w / 1080.0
    size = float(int(round(style.size * scale)))
    font, exact = pick_font(style.font)
    font_name = font.name if font else style.font
    # What the renderer must be told to select exactly this font file: its own family name, and bold only
    # when the file is the bold member of its family (asking for bold otherwise makes a fake bold).
    ass_font = font.ass_name if font else style.font
    ass_bold = -1 if (font.bold if font else style.bold) else 0
    x = out_w / 2
    y = round(min(SAFE_BOTTOM, max(SAFE_TOP, position_y if position_y is not None else style.position)) * out_h, 1)
    report: dict[str, Any] = {"font": font_name, "font_requested": style.font, "font_found": bool(font and exact),
                              "shrunk": 0, "overflow": [], "max_width": 0.0, "position_y": y, "lines": 0,
                              "events": 0}

    primary, secondary = ass_color(style.color), ass_color(style.color)
    if style.animation == "karaoke":
        primary, secondary = ass_color(style.highlight), ass_color(style.color)
    if style.box:
        border_style, outline_color = 3, ass_color(style.box_color, style.box_opacity)
        outline_w, shadow = round(14 * scale, 1), 0
    else:
        border_style, outline_color = 1, ass_color(style.outline_color)
        outline_w, shadow = round(style.outline * scale, 1), round(style.shadow * scale, 1)
    hook_px = float(int(round(style.hook_size * scale)))
    header = "\n".join([
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {out_w}", f"PlayResY: {out_h}", "WrapStyle: 2",
        "ScaledBorderAndShadow: yes", "YCbCr Matrix: None", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        f"Style: Caption,{ass_font},{size:g},{primary},{secondary},{outline_color},{ass_color('#000000', 0.6)},"
        f"{ass_bold},0,0,0,100,100,{style.spacing * scale:g},0,{border_style},{outline_w:g},{shadow:g},"
        "5,40,40,40,1",
        f"Style: Hook,{ass_font},{hook_px:g},{ass_color(style.hook_color)},{ass_color(style.hook_color)},"
        f"{ass_color(style.hook_box)},{ass_color('#000000', 0.0)},{ass_bold},0,0,0,100,100,0,0,3,{round(16 * scale, 1):g},0,"
        "5,40,40,40,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text", ""])

    lines: list[str] = []
    if hook and hook.strip():
        hook_text = _wrap_hook(_display(" ".join(hook.split()), replace(style, uppercase=True)), font, hook_px,
                               out_w * 0.84)
        end = hook_seconds if hook_seconds and hook_seconds > 0 else (clip_seconds or 4.0)
        if clip_seconds:
            end = min(end, clip_seconds)
        hy = round((hook_y if hook_y is not None else 0.16) * out_h, 1)
        lines.append(f"Dialogue: 1,{ass_time(0)},{ass_time(end)},Hook,,0,0,0,,{{\\pos({x:g},{hy:g})}}{hook_text}")

    groups = _fit(group_words(words, style), style, font, size, out_w * MAX_TEXT_WIDTH, report)
    base, hi = _inline(style.color), _inline(style.highlight)
    em = _inline(style.emphasis) if style.emphasis else None
    cursor = 0.0  # end of the previous event: events never overlap

    def emit(start: float, end: float, body: str, line_size: float) -> None:
        nonlocal cursor
        start = max(start, cursor)
        end = max(end, start + 0.05)
        tag = f"\\pos({x:g},{y:g})" + (f"\\fs{line_size:g}" if line_size != size else "")
        lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Caption,,0,0,0,,{{{tag}}}{body}")
        cursor = round(end, 2)
        report["events"] += 1

    for index, (group, line_size) in enumerate(groups):
        texts = [_display(w.text, style) for w in group]
        next_start = groups[index + 1][0][0].start if index + 1 < len(groups) else float("inf")
        line_end = min(group[-1].end + LINE_HOLD, next_start)
        report["lines"] += 1

        if style.animation == "karaoke":
            parts = []
            for k, word in enumerate(group):
                until = group[k + 1].start if k + 1 < len(group) else word.end
                parts.append(f"{{\\kf{max(1, int(round((until - word.start) * 100)))}}}{texts[k]}")
            emit(group[0].start, line_end, " ".join(parts), line_size)
        elif style.animation == "word":
            for k, word in enumerate(group):
                until = group[k + 1].start if k + 1 < len(group) else line_end
                colour = f"\\c{em}" if (word.emph and em) else ""
                pop = "\\fscx82\\fscy82\\t(0,80,\\fscx100\\fscy100)" if style.pop else ""
                body = f"{{{colour}{pop}}}{texts[k]}" if (colour or pop) else texts[k]
                emit(word.start, until, body, line_size)
        elif style.animation == "highlight":
            grow = f"\\fscx{POP_SCALE}\\fscy{POP_SCALE}" if style.pop else ""
            shrink = "\\fscx100\\fscy100" if style.pop else ""
            for active, word in enumerate(group):
                until = group[active + 1].start if active + 1 < len(group) else line_end
                parts = []
                for k, other in enumerate(group):
                    if k == active:
                        parts.append(f"{{\\c{hi}{grow}}}{texts[k]}{{\\c{base}{shrink}}}")
                    elif other.emph and em:
                        parts.append(f"{{\\c{em}}}{texts[k]}{{\\c{base}}}")
                    else:
                        parts.append(texts[k])
                emit(word.start, until, " ".join(parts), line_size)
        else:  # "none": the whole line, with optional emphasis colour
            parts = [f"{{\\c{em}}}{texts[k]}{{\\c{base}}}" if (w.emph and em) else texts[k] for k, w in enumerate(group)]
            emit(group[0].start, line_end, " ".join(parts), line_size)

    return header + "\n".join(lines) + "\n", report


# ------------------------------------------------------------------------------------------- SRT / VTT
def _cues(words: list[CaptionWord], max_chars: int = 42, max_words: int = 9) -> list[tuple[float, float, str]]:
    cues: list[tuple[float, float, str]] = []
    current: list[CaptionWord] = []

    def flush() -> None:
        if current:
            cues.append((current[0].start, current[-1].end, " ".join(w.text for w in current)))
            current.clear()

    for word in words:
        if word.hidden or not word.text.strip():
            continue
        if current:
            line = " ".join(w.text for w in current)
            ended = current[-1].text.rstrip("\"')]”’")[-1:] in ".?!…"
            if (ended or len(current) >= max_words or word.start - current[-1].end > 0.6
                    or len(line) + 1 + len(word.text) > max_chars):
                flush()
        current.append(word)
    flush()
    # a cue stays up until the next one starts (up to a second), which reads better than flashing off
    out = []
    for k, (start, end, text) in enumerate(cues):
        limit = cues[k + 1][0] if k + 1 < len(cues) else end + 1.0
        out.append((start, max(end, min(end + 1.0, limit)), text))
    return out


def _stamp(seconds: float, separator: str) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}{separator}{ms % 1000:03d}"


def build_srt(words: list[CaptionWord]) -> str:
    blocks = [f"{n}\n{_stamp(start, ',')} --> {_stamp(end, ',')}\n{text}"
              for n, (start, end, text) in enumerate(_cues(words), 1)]
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def build_vtt(words: list[CaptionWord]) -> str:
    blocks = [f"{_stamp(start, '.')} --> {_stamp(end, '.')}\n{text}" for start, end, text in _cues(words)]
    return "WEBVTT\n\n" + "\n\n".join(blocks) + ("\n" if blocks else "")
