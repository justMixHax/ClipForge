"""Caption fonts: which are available, what to call them in a subtitle file, and how wide text will be.

Fonts come from two places: open-licence fonts in data/fonts (Noto Sans and friends, fetched by
scripts/get-fonts.ps1) and a short list of bold fonts that ship with Windows. The renderer hands FFmpeg a
folder with exactly the font file in use.

Three facts are read straight from each font file, because getting them wrong makes the renderer quietly
substitute another font or draw text at a different width than was measured:
  * the family name the subtitle renderer matches on (name ID 1, e.g. "Noto Sans Black", not "Noto Sans")
  * whether the file is the bold member of its family (then the style must ask for bold to select it)
  * the Windows line height, which is what the renderer scales the font by
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..config import get_settings

# name shown in the app -> file in C:\Windows\Fonts (the heaviest weight, which reads best as a caption)
SYSTEM_FONTS = {
    "Arial Black": "ariblk.ttf", "Impact": "impact.ttf", "Segoe UI": "segoeuib.ttf", "Arial": "arialbd.ttf",
    "Bahnschrift": "bahnschrift.ttf", "Verdana": "verdanab.ttf", "Trebuchet MS": "trebucbd.ttf",
    "Tahoma": "tahomabd.ttf", "Georgia": "georgiab.ttf", "Comic Sans MS": "comicbd.ttf",
}
FALLBACK_ORDER = ("Arial Black", "Impact", "Noto Sans", "Segoe UI", "Arial")
SFNT_SIGNATURES = (bytes([0, 1, 0, 0]), b"OTTO", b"true")


@dataclass(frozen=True, slots=True)
class FontFacts:
    family: str          # name ID 1: what subtitle renderers match on
    typo_family: str     # name ID 16 when present (the family as designers group it), else the same as family
    weight: int          # 400 regular, 700 bold, 900 black
    bold: bool           # the file is flagged as the bold member of its family
    italic: bool
    cell_ratio: float    # units per em / (usWinAscent + usWinDescent)


@dataclass(frozen=True, slots=True)
class FontInfo:
    name: str            # shown in the app and stored in clip settings
    path: Path
    source: str          # "bundled" (data/fonts) or "system"
    ass_name: str        # written into the subtitle file
    bold: bool           # the subtitle style must request bold to select this file


def _decode(raw: bytes, platform: int) -> str:
    try:
        return raw.decode("utf-16-be" if platform in (0, 3) else "latin-1").strip(chr(0) + " ")
    except UnicodeDecodeError:
        return ""


@lru_cache(maxsize=128)
def read_font_facts(path: str) -> FontFacts | None:
    """Parse the few table entries needed from a TrueType/OpenType file. None for anything unreadable."""
    try:
        with open(path, "rb") as fh:
            header = fh.read(12)
            if header[:4] not in SFNT_SIGNATURES:
                return None  # a collection (.ttc) or not a font
            tables: dict[bytes, tuple[int, int]] = {}
            for _ in range(struct.unpack(">H", header[4:6])[0]):
                tag, _checksum, offset, length = struct.unpack(">4sIII", fh.read(16))
                tables[tag] = (offset, length)
            if not all(tag in tables for tag in (b"head", b"OS/2", b"name")):
                return None
            fh.seek(tables[b"head"][0] + 18)
            units = struct.unpack(">H", fh.read(2))[0]
            fh.seek(tables[b"OS/2"][0])
            os2 = fh.read(78)
            weight = struct.unpack(">H", os2[4:6])[0]
            selection = struct.unpack(">H", os2[62:64])[0]
            win_ascent, win_descent = struct.unpack(">HH", os2[74:78])
            fh.seek(tables[b"name"][0])
            name_table = fh.read(tables[b"name"][1])
    except (OSError, struct.error):
        return None
    if not units or win_ascent + win_descent == 0 or len(name_table) < 6:
        return None

    _format, count, strings = struct.unpack(">HHH", name_table[:6])
    names: dict[int, tuple[int, str]] = {}  # name id -> (preference, text)
    for i in range(count):
        record = name_table[6 + i * 12: 18 + i * 12]
        if len(record) < 12:
            break
        platform, _encoding, language, name_id, length, offset = struct.unpack(">HHHHHH", record)
        if name_id not in (1, 16):
            continue
        text = _decode(name_table[strings + offset: strings + offset + length], platform)
        preference = 3 if (platform == 3 and language == 0x409) else 2 if platform == 3 else 1
        if text and preference > names.get(name_id, (0, ""))[0]:
            names[name_id] = (preference, text)
    family = names.get(1, (0, ""))[1]
    if not family:
        return None
    return FontFacts(family=family, typo_family=names.get(16, (0, family))[1], weight=weight,
                     bold=bool(selection & 0x20), italic=bool(selection & 0x01),
                     cell_ratio=units / (win_ascent + win_descent))


def _info(path: Path, source: str, display: str | None = None) -> tuple[FontInfo, FontFacts] | None:
    facts = read_font_facts(str(path))
    if facts is None:
        return None
    return FontInfo(name=display or facts.typo_family, path=path, source=source, ass_name=facts.family,
                    bold=facts.bold), facts


@lru_cache(maxsize=4)
def _scan(fonts_dir: str, windir: str) -> dict[str, FontInfo]:
    fonts: dict[str, FontInfo] = {}
    heaviest: dict[str, int] = {}
    bundled = Path(fonts_dir)
    if bundled.is_dir():
        for path in sorted(bundled.iterdir()):
            if path.suffix.lower() not in (".ttf", ".otf"):
                continue
            found = _info(path, "bundled")
            if found is None or found[1].italic:
                continue
            info, facts = found
            if facts.weight > heaviest.get(info.name, -1):  # one entry per family: its heaviest upright file
                heaviest[info.name] = facts.weight
                fonts[info.name] = info
    system = Path(windir) / "Fonts" if windir else None
    if system and system.is_dir():
        for name, file in SYSTEM_FONTS.items():
            if name not in fonts and (system / file).exists():
                found = _info(system / file, "system", display=name)
                if found is not None:
                    fonts[name] = found[0]
    return fonts


def available_fonts() -> dict[str, FontInfo]:
    return _scan(str(get_settings().fonts_dir), os.environ.get("WINDIR", ""))


def refresh() -> None:
    _scan.cache_clear()


def find_font(name: str) -> FontInfo | None:
    """Look a font up by the name shown in the app or by the name used in subtitle files."""
    lowered = name.strip().lower()
    for info in available_fonts().values():
        if lowered in (info.name.lower(), info.ass_name.lower()):
            return info
    return None


def pick_font(name: str) -> tuple[FontInfo | None, bool]:
    """(font to use, whether it is the one asked for). Falls back to the nearest sensible caption font."""
    exact = find_font(name)
    if exact is not None:
        return exact, True
    fonts = available_fonts()
    for fallback in FALLBACK_ORDER:
        if fallback in fonts:
            return fonts[fallback], False
    return (next(iter(fonts.values())), False) if fonts else (None, False)


@lru_cache(maxsize=64)
def _loaded(path: str, pixels: int):  # noqa: ANN202
    from PIL import ImageFont
    return ImageFont.truetype(path, pixels)


def text_width(text: str, font: FontInfo | None, ass_size: float, spacing: float = 0.0) -> float:
    """Width in output pixels of text burned in at the given subtitle font size.

    The subtitle renderer scales a font so that its Windows line height equals the font size; Pillow scales
    by the em square. cell_ratio converts between the two (0.71 for Arial Black, 0.58 for Anton)."""
    if not text:
        return 0.0
    facts = read_font_facts(str(font.path)) if font is not None else None
    if font is None or facts is None:
        return 0.6 * ass_size * len(text)  # no font file to measure: a cautious estimate
    try:
        pixels = max(4, int(round(ass_size * facts.cell_ratio)))
        width = float(_loaded(str(font.path), pixels).getlength(text))
    except Exception:  # noqa: BLE001
        return 0.6 * ass_size * len(text)
    return width + spacing * max(0, len(text) - 1)
