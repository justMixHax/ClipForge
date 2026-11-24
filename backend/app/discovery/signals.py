"""What YouTube's public data says about a video before it is downloaded. Everything here is counted from what
the official API returned: how many of the top comments are laughing, which moments viewers wrote down, and how
a video's views compare with the channel's usual. No prediction and no made-up number: a count is a count.

Pure functions, tested without a network.
"""
from __future__ import annotations

import re
import statistics
import unicodedata
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import unquote, urlsplit

from ..services.timecode import youtube_id

# ------------------------------------------------------------------------------- what a channel is called
_CHANNEL_ID = re.compile(r"UC[\w-]{22}")
_YOUTUBE_HOSTS = {"youtube.com", "m.youtube.com", "music.youtube.com", "gaming.youtube.com"}
# first parts of a YouTube address that are pages of the site, not a channel's own name
_SITE_PAGES = {"watch", "shorts", "live", "embed", "v", "feed", "results", "playlist", "channel", "user", "c",
               "gaming", "premium", "account", "trending", "hashtag", "post", "clip", "source", "t", "about",
               "upload", "signin", "logout", "music", "kids", "tv", "redirect", "attribution_link"}


def channel_query(text: str) -> tuple[str, str]:
    """How to look a channel up from what was typed or pasted: ("id", "UC…"), ("handle", "@name"),
    ("username", "name") for an old /user/ address, ("custom", "name") for an old /c/ address, ("video", id) for
    a link to one of its videos, ("words", text) for a name to search for, ("other", …) for a link that is not
    a YouTube channel or video, and ("empty", "")."""
    text = " ".join((text or "").split())
    if not text:
        return "empty", ""
    if _CHANNEL_ID.fullmatch(text):
        return "id", text
    if text.startswith("@") and " " not in text and len(text) > 1:
        return "handle", text
    try:
        parts = urlsplit(text if "://" in text else "https://" + text)
        host = (parts.hostname or "").lower()
    except ValueError:
        return "words", text
    host = host[4:] if host.startswith("www.") else host
    on_youtube = host in _YOUTUBE_HOSTS or host == "youtu.be"
    looks_like_a_link = "://" in text or on_youtube or ("/" in text and "." in host and " " not in text)
    if not looks_like_a_link:
        return "words", text
    if not on_youtube:
        return "other", host
    video = youtube_id(text if "://" in text else "https://" + text)
    if video:
        return "video", video
    path = [unquote(part) for part in parts.path.split("/") if part]
    if not path:
        return "other", host
    first = path[0]
    if first == "channel" and len(path) > 1 and _CHANNEL_ID.fullmatch(path[1]):
        return "id", path[1]
    if first.startswith("@") and len(first) > 1:
        return "handle", first
    if first == "user" and len(path) > 1:
        return "username", path[1]
    if first == "c" and len(path) > 1:
        return "custom", path[1]
    if len(path) == 1 and first.lower() not in _SITE_PAGES:
        return "custom", first
    return "other", host


# ------------------------------------------------------------------------------------- lengths and dates
_DURATION = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?")


def iso_duration(text: Any) -> float:
    """Seconds from a length as YouTube writes it (PT1H2M10S). 0 for a live stream (P0D) or anything unreadable."""
    found = _DURATION.fullmatch(str(text or "").strip().upper())
    if not found or not any(found.groups()):
        return 0.0
    days, hours, minutes, seconds = (float(group) if group else 0.0 for group in found.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def parse_when(text: Any) -> datetime | None:
    """A date as YouTube writes it (2026-11-20T18:00:05Z), as timezone-aware UTC; None when it is not one."""
    try:
        moment = datetime.fromisoformat(str(text or "").strip())
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def pacific_day(now: datetime) -> str:
    """The date in Pacific time, where YouTube's daily quota starts again at midnight. Worked out from the US
    rule (summer time from the second Sunday of March to the first Sunday of November) so no time-zone data
    has to be installed."""
    now = now.astimezone(timezone.utc)

    def first_sunday(month: int, day: int, hour: int) -> datetime:
        start = datetime(now.year, month, day, hour, tzinfo=timezone.utc)
        return start + timedelta(days=(6 - start.weekday()) % 7)

    summer = first_sunday(3, 8, 10) <= now < first_sunday(11, 1, 9)   # 02:00 local, in UTC
    return (now - timedelta(hours=7 if summer else 8)).date().isoformat()


# --------------------------------------------------------------------------------------------- laughter
_LAUGH_EMOJI = "😂🤣😆😹💀😭☠"
_LAUGH_WORDS = re.compile(
    r"\b(?:l+o+l+(?:ol)*|lmf?a+o+|rofl+|a?(?:ha){2,}h?|(?:he){2,}|(?:ja){2,}|kekw?|omegalul|lulw?"
    r"|funn(?:y|ier|iest)|hilarious|laugh(?:s|ed|ing)?|i['’]?m (?:dead|dying|crying|wheezing|weak))\b"
    r"|ㅋ{2,}|ㅎ{2,}", re.IGNORECASE)


def is_laughing(text: str) -> bool:
    """Whether a comment laughs: a laughing emoji, or lol, lmao, haha, "I'm dead" and the like."""
    text = text or ""
    return any(mark in text for mark in _LAUGH_EMOJI) or bool(_LAUGH_WORDS.search(text))


# ---------------------------------------------------------------------------------- moments in comments
_TIME = re.compile(r"(?<![\w:.,])(?:(\d{1,2}):)?(\d{1,3}):([0-5]\d)(?![\d:])(?!\s?[ap]\.?m\b)", re.IGNORECASE)
CHAPTER_LIST = 5         # a comment with this many times is a list of chapters, not somebody pointing at a moment
EARLIEST = 3.0           # "0:00" is where every list starts, not a moment
SAME_MOMENT = 20.0       # times within this many seconds are about the same moment


def _times(text: str) -> list[float]:
    """Every time written in a text, in seconds, whether or not it fits the video."""
    found = []
    for hours, minutes, seconds in _TIME.findall(text or ""):
        if hours and int(minutes) >= 60:
            continue
        found.append(float(int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)))
    return found


def comment_times(text: str, duration: float) -> list[float]:
    """The moments of the video a comment points at: times written in it that fit inside the video. A list of
    chapters points at nothing in particular and gives none."""
    found = _times(text)
    if len(found) >= CHAPTER_LIST:
        return []
    return [t for t in found if t >= EARLIEST and (duration <= 0 or t < duration)]


def clean_excerpt(text: str, limit: int = 140) -> str:
    """A comment made safe and short for showing: one line, no control characters, at most limit characters."""
    kept = "".join(ch for ch in (text or "") if ch.isspace() or unicodedata.category(ch) != "Cc")
    kept = re.sub(r"[‪-‮⁦-⁩]", "", kept)   # no tricks with the direction of the text
    kept = " ".join(kept.split())
    return kept if len(kept) <= limit else kept[:limit - 1].rstrip() + "…"


def pointed_moments(comments: Sequence[dict[str, Any]], duration: float, limit: int = 8) -> list[dict[str, Any]]:
    """The moments viewers point at in the comments, the most pointed-at first. Comments naming times close
    together count as one moment: its time and words are those of the most-liked comment, mentions is how many
    comments named it, likes is their likes added up."""
    mentions = sorted(
        (t, int(c.get("likes") or 0), str(c.get("text") or ""))
        for c in comments for t in comment_times(str(c.get("text") or ""), duration))
    groups: list[list[tuple[float, int, str]]] = []
    for mention in mentions:
        if groups and mention[0] - groups[-1][0][0] <= SAME_MOMENT:
            groups[-1].append(mention)
        else:
            groups.append([mention])
    moments = []
    for group in groups:
        best = max(group, key=lambda m: (m[1], -m[0]))
        moments.append({"t": best[0], "mentions": len(group), "likes": sum(m[1] for m in group),
                        "text": clean_excerpt(best[2]), "laughing": any(is_laughing(m[2]) for m in group)})
    moments.sort(key=lambda m: (-(m["likes"] + m["mentions"]), m["t"]))
    return moments[:limit]


def comment_signals(comments: Sequence[dict[str, Any]], duration: float, now: datetime) -> dict[str, Any]:
    """What a video's top comments say, as plain counts: how many were read, how many of them laugh, and the
    moments they point at."""
    return {"checked": len(comments), "laughing": sum(1 for c in comments if is_laughing(str(c.get("text") or ""))),
            "moments": pointed_moments(comments, duration), "fetched_at": now.isoformat()}


# -------------------------------------------------------------------------------------------- standing out
MIN_TO_COMPARE = 4


def standout(views: int | None, others: Sequence[int]) -> float | None:
    """A video's views as a multiple of the middle value of the channel's other videos (3.3 = 3.3 times the
    usual). None when its views are hidden or there are too few other videos to compare with."""
    if views is None or len(others) < MIN_TO_COMPARE:
        return None
    usual = statistics.median(others)
    if usual <= 0:
        return None
    return round(views / usual, 2)
