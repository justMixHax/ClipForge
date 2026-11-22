"""File name and path validation for anything that arrives from the browser.

Rules: uploaded names are reduced to a harmless base name, stored paths must stay inside the data
directory, and a path the user asks to import must be a real local video file (no network shares, no
device paths, no relative paths).
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class PathSecurityError(ValueError):
    """Raised when a file name or path is not acceptable. The message is safe to show to the user."""


def safe_filename(name: str, default: str = "file", max_length: int = 120) -> str:
    """Turn any client-supplied name into a plain file name with no directory parts."""
    name = (name or "").replace("\\", "/").split("/")[-1]
    name = _ILLEGAL.sub("", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        return default
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        stem, ext = name, ""
    if stem.lower() in _RESERVED:
        stem = "_" + stem
    suffix = f".{ext}" if ext else ""
    room = max(1, max_length - len(suffix))
    return stem[:room].rstrip(" .") + suffix


def has_allowed_extension(name: str, allowed: Iterable[str]) -> bool:
    return Path(name).suffix.lower() in {e.lower() for e in allowed}


def resolve_inside(base: Path, relative: str) -> Path:
    """Join a stored relative path onto base, refusing anything that would escape it."""
    if not relative or not relative.strip():
        raise PathSecurityError("empty path")
    normalised = relative.replace("\\", "/")
    if normalised.startswith("/") or re.match(r"^[A-Za-z]:", normalised):
        raise PathSecurityError("absolute paths are not allowed here")
    parts = [p for p in normalised.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise PathSecurityError("path leaves the data folder")
    candidate = base.joinpath(*parts)
    if not candidate.resolve().is_relative_to(base.resolve()):
        raise PathSecurityError("path leaves the data folder")
    return candidate


def validate_import_path(raw: str, allowed_extensions: Iterable[str]) -> Path:
    """Check a path typed by the user for "add a video that is already on this PC"."""
    text = (raw or "").strip().strip('"').strip("'").strip()
    if not text:
        raise PathSecurityError("no path given")
    if "://" in text:
        raise PathSecurityError("that is a web address, not a file on this PC")
    if text.startswith("\\\\") or text.startswith("//"):
        raise PathSecurityError("network shares and device paths are not allowed; copy the file to a local disk")
    path = Path(text)
    if not path.is_absolute():
        raise PathSecurityError("give the full path, for example C:\\Videos\\stream.mp4")
    if not path.exists():
        raise PathSecurityError(f"file not found: {path.name}")
    if not path.is_file():
        raise PathSecurityError("that path is a folder, not a file")
    if not has_allowed_extension(path.name, allowed_extensions):
        allowed = ", ".join(sorted(e.lstrip(".").upper() for e in allowed_extensions))
        raise PathSecurityError(f"unsupported file type '{path.suffix or 'none'}' (supported: {allowed})")
    return path
