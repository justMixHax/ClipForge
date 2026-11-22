"""Reading a moment's time the way people write it: 1:02:15, 62:15, 3735, 1h2m15s, or a link shared at a moment
(YouTube's "Share → Start at" and "Copy video URL at current time", Twitch's "?t=01h02m15s")."""
from __future__ import annotations

import math
import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

from ..errors import ClipForgeError

FORMATS = "Write it like 1:02:15, 62:15, 3735 (seconds) or 1h2m15s, or paste a link shared at that moment."
LINK_HINT = ("On YouTube, use Share → Start at, or right-click the video and choose “Copy video URL at current "
             "time”. Or type the time instead, like 1:02:15.")

_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_CLOCK = re.compile(r"\d+(?::\d{1,2}){1,2}(?:\.\d+)?")
_UNITS = re.compile(r"(?:(\d+(?:\.\d+)?)h)?\s*(?:(\d+(?:\.\d+)?)m)?\s*(?:(\d+(?:\.\d+)?)s)?")
_LINK = re.compile(r"^(?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?::\d+)?(?:[/?#]|$)", re.IGNORECASE)
_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_HOSTS = {"youtube.com", "m.youtube.com", "music.youtube.com", "youtube-nocookie.com"}


class TimeError(ClipForgeError):
    pass


def _plain(text: str) -> float | None:
    """Seconds from a time written without a link, or None when it is not one."""
    text = text.strip().lower().replace(",", ".")
    if not text:
        return None
    if _NUMBER.fullmatch(text):
        return float(text)
    if _CLOCK.fullmatch(text):
        parts = text.split(":")
        if len(parts) == 2:
            minutes, seconds = int(parts[0]), float(parts[1])
            return None if seconds >= 60 else minutes * 60 + seconds
        hours, minutes, seconds = int(parts[0]), int(parts[1]), float(parts[2])
        return None if minutes >= 60 or seconds >= 60 else hours * 3600 + minutes * 60 + seconds
    units = _UNITS.fullmatch(text)
    if units and any(units.groups()):
        hours, minutes, seconds = (float(g) if g else 0.0 for g in units.groups())
        return hours * 3600 + minutes * 60 + seconds
    return None


def _split(text: str):  # noqa: ANN202
    try:
        return urlsplit(text.strip() if "://" in text else "https://" + text.strip())
    except ValueError:
        return None


def _link_time(text: str) -> float | None:
    parts = _split(text)
    if parts is None:
        return None
    for place in (parts.query, parts.fragment):
        values = parse_qs(place)
        for key in ("t", "start", "time_continue"):
            if values.get(key):
                return _plain(values[key][0])
    return None


def _unreadable(value: Any) -> TimeError:
    shown = "" if value is None else str(value).strip()[:40]
    if not shown:
        return TimeError("No time was given. " + FORMATS)
    return TimeError(f"“{shown}” is not a time ClipForge can read. " + FORMATS)


def parse_time(value: Any) -> float:
    """Seconds from the start of the video. Raises TimeError with a message that shows how to write it."""
    if value is None or isinstance(value, bool):
        raise _unreadable(value)
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or value < 0:
            raise _unreadable(value)
        return float(value)
    text = str(value).strip()
    if _LINK.match(text):
        seconds = _link_time(text)
        if seconds is None:
            raise TimeError("That link has no time in it, so ClipForge cannot tell where the moment starts.",
                            hint=LINK_HINT)
        return seconds
    seconds = _plain(text)
    if seconds is None or not math.isfinite(seconds):
        raise _unreadable(text)
    return seconds


def youtube_id(text: str | None) -> str | None:
    """The 11-character id of the YouTube video a link points at, or None for anything else."""
    if not text or not _LINK.match(text.strip()):
        return None
    parts = _split(text)
    if parts is None:
        return None
    host = (parts.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    candidate = ""
    if host == "youtu.be":
        candidate = parts.path.strip("/").split("/")[0]
    elif host in _YOUTUBE_HOSTS:
        if parts.path.rstrip("/") == "/watch":
            candidate = (parse_qs(parts.query).get("v") or [""])[0]
        else:
            found = re.match(r"^/(?:shorts|live|embed|v)/([^/?#]+)", parts.path)
            candidate = found.group(1) if found else ""
    return candidate if _YOUTUBE_ID.match(candidate) else None
