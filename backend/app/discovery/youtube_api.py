"""The official YouTube Data API v3, read-only, with your own API key.

Only public information is asked for: channels, their uploads, the public numbers of videos, and top comments.
The key travels in a request header (X-goog-api-key), never in a web address, so it cannot end up in a log or
in an error message. Every request has a cost in YouTube's quota units (10,000 a day for a free project); the
units spent are counted here so the app can show them.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Any

import httpx

from ..errors import ClipForgeError
from .signals import iso_duration, parse_when

# httpx logs every request address at INFO; keep the log to what ClipForge itself says.
logging.getLogger("httpx").setLevel(logging.WARNING)

API = "https://www.googleapis.com/youtube/v3"
OVERRIDE: str | None = None   # the tests point the client at a local stand-in
USER_AGENT = "ClipForge/0.1 (personal clipping studio)"
COSTS = {"channels": 1, "playlistItems": 1, "videos": 1, "commentThreads": 1, "search": 100}
DAILY_UNITS = 10_000          # what a free Google Cloud project gets a day
BATCH = 50                    # the most ids YouTube takes in one request

KEY_HINT = ("Copy it again from Google Cloud → APIs & Services → Credentials, and paste it under Creator "
            "Discovery → API key.")
ENABLE_HINT = ("In Google Cloud, open APIs & Services → Library, find “YouTube Data API v3” and press Enable. It "
               "can take a few minutes to start working.")
RESTRICT_HINT = ("In Google Cloud → APIs & Services → Credentials, open the key and set “Application "
                 "restrictions” to None, and “API restrictions” to YouTube Data API v3 (or no restriction).")
QUOTA_HINT = ("It starts again at midnight Pacific time. Looking a channel up by its link or @handle costs 1 "
              "unit; searching by name costs 100.")


class DiscoveryError(ClipForgeError):
    """YouTube could not be asked, or refused. key: the API key is the problem. quota: today's units are used
    up. retry: trying again later may work."""

    def __init__(self, message: str, *, hint: str = "", key: bool = False, quota: bool = False,
                 retry: bool = False, status_code: int = 400) -> None:
        super().__init__(message, hint=hint)
        self.key = key
        self.quota = quota
        self.retry = retry
        self.status_code = status_code
        self.reasons: set[str] = set()   # YouTube's own reason codes, when it refused


def _plain(text: Any, limit: int = 200) -> str:
    """Google's own words about an error, without the HTML it sometimes puts in them."""
    return " ".join(re.sub(r"<[^>]+>", "", str(text or "")).split())[:limit]


def _reasons(body: Any) -> tuple[set[str], str]:
    """(every reason code in an error answer, its message)."""
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return set(), ""
    found = {str(item.get("reason") or "") for item in error.get("errors") or [] if isinstance(item, dict)}
    found |= {str(item.get("reason") or "") for item in error.get("details") or [] if isinstance(item, dict)}
    found.add(str(error.get("status") or ""))
    return found - {""}, _plain(error.get("message"))


def _number(value: Any) -> int | None:
    """YouTube sends its counts as text; a hidden count is simply not there."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _thumb(snippet: dict[str, Any], *sizes: str) -> str:
    pictures = snippet.get("thumbnails") or {}
    for size in sizes:
        url = (pictures.get(size) or {}).get("url")
        if url and str(url).startswith("https://"):
            return str(url)
    return ""


class YouTubeData:
    """One conversation with the API. spent is the quota units used by the requests sent through it."""

    def __init__(self, key: str, *, base: str | None = None, client: httpx.Client | None = None,
                 timeout: float = 20.0) -> None:
        self.key = key.strip()
        self.base = (base or OVERRIDE or API).rstrip("/")
        self.spent = 0
        self._client = client or httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0), follow_redirects=False,
                                              headers={"User-Agent": USER_AGENT, "Accept": "application/json"})

    def close(self) -> None:
        self._client.close()

    # --------------------------------------------------------------------------------------- one request
    def _get(self, resource: str, params: dict[str, Any]) -> dict[str, Any]:
        self.spent += COSTS[resource]   # YouTube charges for a request whatever comes of it
        try:
            response = self._client.get(f"{self.base}/{resource}", params=params,
                                        headers={"X-goog-api-key": self.key})
        except httpx.TimeoutException as exc:
            raise DiscoveryError("YouTube did not answer in time.", hint="Try again in a moment.", retry=True,
                                 status_code=504) from exc
        except httpx.TransportError as exc:
            raise DiscoveryError(f"YouTube could not be reached ({type(exc).__name__}).",
                                 hint="Check the internet connection.", retry=True, status_code=502) from exc
        try:
            body = response.json()
        except ValueError:
            body = None
        if response.status_code == 200 and isinstance(body, dict):
            return body
        raise self._error(response.status_code, body)

    def _error(self, status: int, body: Any) -> DiscoveryError:
        """A refusal in words a person can act on. reasons on the error holds YouTube's own reason codes, for the
        callers that expect a particular refusal (comments switched off, a channel without uploads)."""
        reasons, said = _reasons(body)
        # Every 403 carries the general status PERMISSION_DENIED, so the particular reasons are looked at first.
        if "API_KEY_INVALID" in reasons or "keyInvalid" in reasons or "API key not valid" in said:
            error = DiscoveryError("YouTube did not accept your API key.", hint=KEY_HINT, key=True)
        elif reasons & {"quotaExceeded", "dailyLimitExceeded"}:
            error = DiscoveryError("Your YouTube API units for today are used up.", hint=QUOTA_HINT, quota=True,
                                   status_code=429)
        elif reasons & {"rateLimitExceeded", "userRateLimitExceeded"} or status == 429:
            error = DiscoveryError("YouTube says there have been too many requests in a short time.",
                                   hint="Try again in a minute.", retry=True, status_code=429)
        elif reasons & {"accessNotConfigured", "SERVICE_DISABLED"}:
            error = DiscoveryError("The YouTube Data API v3 is not switched on in your Google Cloud project.",
                                   hint=ENABLE_HINT, key=True)
        elif any(reason.startswith("API_KEY_") and reason.endswith("_BLOCKED") for reason in reasons):
            error = DiscoveryError("Your API key's restrictions do not allow this request.", hint=RESTRICT_HINT,
                                   key=True)
        elif reasons & {"ACCESS_DENIED", "UNAUTHENTICATED"}:   # what YouTube answers when it finds no usable key
            error = DiscoveryError("YouTube refused the request" + (f": {said}" if said else "."), hint=KEY_HINT,
                                   key=True)
        elif status >= 500:
            error = DiscoveryError(f"YouTube had a problem on its side ({status}).", hint="Try again in a moment.",
                                   retry=True, status_code=502)
        else:
            error = DiscoveryError(f"YouTube refused the request ({status})" + (f": {said}" if said else "."))
        error.reasons = reasons
        return error

    # ------------------------------------------------------------------------------------------ channels
    @staticmethod
    def _channel(item: dict[str, Any]) -> dict[str, Any]:
        snippet = item.get("snippet") or {}
        numbers = item.get("statistics") or {}
        handle = str(snippet.get("customUrl") or "").strip()
        channel_id = str(item.get("id") or "")
        return {
            "id": channel_id, "title": str(snippet.get("title") or "").strip()[:300],
            "handle": handle if handle.startswith("@") else "",
            "url": f"https://www.youtube.com/{handle}" if handle.startswith("@")
                   else f"https://www.youtube.com/channel/{channel_id}",
            "description": " ".join(str(snippet.get("description") or "").split())[:400],
            "thumb_url": _thumb(snippet, "medium", "default", "high"),
            "subscribers": None if numbers.get("hiddenSubscriberCount") else _number(numbers.get("subscriberCount")),
            "video_count": _number(numbers.get("videoCount")),
            "uploads": str(((item.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads") or ""),
        }

    def channels(self, *, ids: Sequence[str] = (), handle: str = "", username: str = "") -> list[dict[str, Any]]:
        """Channels by id (in the order asked for), by @handle, or by an old user name. Unknown ones are
        simply not in the answer."""
        parts = "snippet,statistics,contentDetails"
        if handle:
            items = self._get("channels", {"part": parts, "forHandle": handle}).get("items") or []
        elif username:
            items = self._get("channels", {"part": parts, "forUsername": username}).get("items") or []
        else:
            wanted = list(dict.fromkeys(i for i in ids if i))
            items = []
            for start in range(0, len(wanted), BATCH):
                items += self._get("channels", {"part": parts, "id": ",".join(wanted[start:start + BATCH]),
                                                "maxResults": BATCH}).get("items") or []
            order = {channel_id: position for position, channel_id in enumerate(wanted)}
            items.sort(key=lambda item: order.get(str(item.get("id")), len(order)))
        return [self._channel(item) for item in items if isinstance(item, dict) and item.get("id")]

    def search_channels(self, words: str, limit: int = 8) -> list[dict[str, Any]]:
        """Channels found by name, best match first. This is the one expensive request: 100 units."""
        found = self._get("search", {"part": "snippet", "type": "channel", "q": words, "maxResults": limit})
        ids = [str((item.get("id") or {}).get("channelId") or "") for item in found.get("items") or []
               if isinstance(item, dict)]
        return self.channels(ids=[i for i in ids if i]) if any(ids) else []

    # -------------------------------------------------------------------------------------------- videos
    def uploads(self, playlist_id: str, limit: int = 25) -> list[str]:
        """The ids of a channel's newest uploads, newest first. A channel without uploads has none."""
        try:
            found = self._get("playlistItems", {"part": "contentDetails", "playlistId": playlist_id,
                                                "maxResults": min(max(1, limit), BATCH)})
        except DiscoveryError as exc:
            if "playlistNotFound" in exc.reasons:
                return []
            raise
        ids = [str((item.get("contentDetails") or {}).get("videoId") or "") for item in found.get("items") or []
               if isinstance(item, dict)]
        return list(dict.fromkeys(i for i in ids if i))

    @staticmethod
    def _video(item: dict[str, Any]) -> dict[str, Any]:
        snippet = item.get("snippet") or {}
        numbers = item.get("statistics") or {}
        live = str(snippet.get("liveBroadcastContent") or "none")
        stream = item.get("liveStreamingDetails") or {}
        return {
            "id": str(item.get("id") or ""), "channel_id": str(snippet.get("channelId") or ""),
            "title": str(snippet.get("title") or "").strip()[:300],
            "description": " ".join(str(snippet.get("description") or "").split())[:600],
            "published_at": parse_when(snippet.get("publishedAt")),
            "duration": iso_duration((item.get("contentDetails") or {}).get("duration")),
            "live": live if live in ("live", "upcoming") else "none",
            "was_live": bool(stream.get("actualEndTime")),
            "views": _number(numbers.get("viewCount")), "likes": _number(numbers.get("likeCount")),
            "comments": _number(numbers.get("commentCount")),
            "thumb_url": _thumb(snippet, "medium", "high", "default"),
        }

    def videos(self, ids: Sequence[str]) -> list[dict[str, Any]]:
        """Public details and numbers of videos. Deleted and private ones are simply not in the answer."""
        wanted = list(dict.fromkeys(i for i in ids if i))
        out: list[dict[str, Any]] = []
        for start in range(0, len(wanted), BATCH):
            found = self._get("videos", {"part": "snippet,contentDetails,statistics,liveStreamingDetails",
                                         "id": ",".join(wanted[start:start + BATCH]), "maxResults": BATCH})
            out += [self._video(item) for item in found.get("items") or [] if isinstance(item, dict) and item.get("id")]
        return out

    def top_comments(self, video_id: str, limit: int = 100) -> list[dict[str, Any]] | None:
        """A video's top comments as YouTube ranks them: their text and likes. None when comments are switched
        off for the video (or it is gone)."""
        try:
            found = self._get("commentThreads", {"part": "snippet", "videoId": video_id, "order": "relevance",
                                                 "maxResults": min(max(1, limit), 100), "textFormat": "plainText"})
        except DiscoveryError as exc:
            if exc.reasons & {"commentsDisabled", "videoNotFound"}:
                return None
            raise
        comments = []
        for item in found.get("items") or []:
            top = (((item.get("snippet") or {}).get("topLevelComment") or {}).get("snippet") or {}) \
                if isinstance(item, dict) else {}
            text = str(top.get("textOriginal") or top.get("textDisplay") or "")
            if text.strip():
                comments.append({"text": text[:2000], "likes": _number(top.get("likeCount")) or 0})
        return comments
