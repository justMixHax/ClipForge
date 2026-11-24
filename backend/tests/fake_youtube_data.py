"""A local stand-in for the read-only part of the YouTube Data API v3 that Creator Discovery uses, so it can be
tested end to end without a real key or quota. Built from YouTube's documentation, and its two "no key" and
"wrong key" answers are the ones the real API gave on 24 November 2026. Every request is recorded.

Like the real API it sends counts as text, leaves "items" out when nothing matches, charges every request, and
reads the key from the X-goog-api-key header."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

KEY = "AIzaSy-test-key-0000000000000000000000"
COSTS = {"channels": 1, "playlistItems": 1, "videos": 1, "commentThreads": 1, "search": 100}


@dataclass
class Asked:
    resource: str
    query: dict[str, str]
    headers: dict[str, str]


@dataclass
class FakeYouTubeData:
    base: str = ""
    key: str = KEY
    asked: list[Asked] = field(default_factory=list)
    channels: dict[str, dict[str, Any]] = field(default_factory=dict)
    uploads: dict[str, list[str]] = field(default_factory=dict)       # channel id -> video ids, newest first
    videos: dict[str, dict[str, Any]] = field(default_factory=dict)
    comments: dict[str, list[tuple[str, int]]] = field(default_factory=dict)
    comments_off: set[str] = field(default_factory=set)
    units_left: int | None = None        # None: no limit. Otherwise the day's quota, as the real API counts it
    disabled: bool = False               # the API is not switched on in the Google Cloud project
    restricted: bool = False             # the key's restrictions refuse the request
    # answer the next requests with these: a status (an error of YouTube's own), or (status, body) to send
    # exactly that, for an answer that is not what the documentation describes
    answer_next: list[int | tuple[int, dict[str, Any]]] = field(default_factory=list)

    @property
    def url(self) -> str:
        return f"{self.base}/youtube/v3"

    def units(self) -> int:
        return sum(COSTS[a.resource] for a in self.asked)

    def calls(self, resource: str) -> list[Asked]:
        return [a for a in self.asked if a.resource == resource]

    # ------------------------------------------------------------------------------------ what it holds
    def add_channel(self, channel_id: str, title: str, handle: str = "", subscribers: int | None = 1_000_000,
                    username: str = "", description: str = "") -> str:
        self.channels[channel_id] = {"title": title, "handle": handle, "subscribers": subscribers,
                                     "username": username, "description": description}
        self.uploads.setdefault(channel_id, [])
        return channel_id

    def add_video(self, video_id: str, channel_id: str, title: str, *, minutes: float = 30.0, views: int | None = 100_000,
                  likes: int | None = 5_000, comments: int | None = 300, published: str = "2026-11-20T18:00:00Z",
                  live: str = "none", was_live: bool = False, description: str = "") -> str:
        self.videos[video_id] = {"channel": channel_id, "title": title, "seconds": int(minutes * 60), "views": views,
                                 "likes": likes, "comments": comments, "published": published, "live": live,
                                 "was_live": was_live, "description": description}
        self.uploads.setdefault(channel_id, []).insert(0, video_id)
        return video_id

    # --------------------------------------------------------------------------------------- answers
    @staticmethod
    def _uploads_id(channel_id: str) -> str:
        return "UU" + channel_id[2:]

    def _channel_item(self, channel_id: str) -> dict[str, Any]:
        c = self.channels[channel_id]
        numbers: dict[str, Any] = {"viewCount": "123456789", "videoCount": str(len(self.uploads.get(channel_id, []))),
                                   "hiddenSubscriberCount": c["subscribers"] is None}
        if c["subscribers"] is not None:
            numbers["subscriberCount"] = str(c["subscribers"])
        picture = f"https://yt3.ggpht.com/{channel_id}"
        return {"kind": "youtube#channel", "id": channel_id,
                "snippet": {"title": c["title"], "description": c["description"], "customUrl": c["handle"].lower(),
                            "publishedAt": "2019-01-01T00:00:00Z",
                            "thumbnails": {"default": {"url": picture + "=s88"}, "medium": {"url": picture + "=s240"},
                                           "high": {"url": picture + "=s800"}}},
                "contentDetails": {"relatedPlaylists": {"likes": "", "uploads": self._uploads_id(channel_id)}},
                "statistics": numbers}

    @staticmethod
    def _length(seconds: int) -> str:
        if seconds <= 0:
            return "P0D"
        h, m, s = seconds // 3600, seconds % 3600 // 60, seconds % 60
        return "PT" + (f"{h}H" if h else "") + (f"{m}M" if m else "") + (f"{s}S" if s else "")

    def _video_item(self, video_id: str) -> dict[str, Any]:
        v = self.videos[video_id]
        numbers = {name: str(v[key]) for name, key in (("viewCount", "views"), ("likeCount", "likes"),
                                                       ("commentCount", "comments")) if v[key] is not None}
        picture = f"https://i.ytimg.com/vi/{video_id}"
        item: dict[str, Any] = {
            "kind": "youtube#video", "id": video_id,
            "snippet": {"publishedAt": v["published"], "channelId": v["channel"], "title": v["title"],
                        "description": v["description"], "channelTitle": self.channels[v["channel"]]["title"],
                        "liveBroadcastContent": v["live"],
                        "thumbnails": {"default": {"url": picture + "/default.jpg"},
                                       "medium": {"url": picture + "/mqdefault.jpg"},
                                       "high": {"url": picture + "/hqdefault.jpg"}}},
            "contentDetails": {"duration": self._length(0 if v["live"] != "none" else v["seconds"])},
            "statistics": numbers}
        if v["was_live"]:
            item["liveStreamingDetails"] = {"actualStartTime": v["published"], "actualEndTime": v["published"]}
        return item


def _error(status: int, message: str, reason: str, domain: str = "youtube", state: str = "",
           detail: str = "") -> tuple[int, dict[str, Any]]:
    error: dict[str, Any] = {"code": status, "message": message,
                             "errors": [{"message": message, "domain": domain, "reason": reason}]}
    if state:
        error["status"] = state
    if detail:
        error["details"] = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": detail,
                             "domain": "googleapis.com"}]
    return status, {"error": error}


def _handler(fake: FakeYouTubeData):  # noqa: C901  (one small answer per kind of request, kept together)
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: Any) -> None:
            pass

        def _send(self, status: int, body: dict[str, Any]) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=UTF-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            parts = urlsplit(self.path)
            query = {k: v[0] for k, v in parse_qs(parts.query).items()}
            resource = parts.path.rsplit("/", 1)[-1]
            if not parts.path.startswith("/youtube/v3/") or resource not in COSTS:
                return self._send(404, {"error": {"code": 404, "message": "Not Found"}})
            fake.asked.append(Asked(resource, query, {k.lower(): v for k, v in self.headers.items()}))
            self._send(*self._answer(resource, query))

        def _answer(self, resource: str, query: dict[str, str]) -> tuple[int, dict[str, Any]]:
            sent = self.headers.get("X-goog-api-key") or query.get("key")
            if not sent:   # the real API's answer on 24 November 2026
                return _error(403, "Permission denied on the service.", "forbidden", "global", "PERMISSION_DENIED",
                              "ACCESS_DENIED")
            if sent != fake.key:
                return _error(400, "API key not valid. Please pass a valid API key.", "badRequest", "global",
                              "INVALID_ARGUMENT", "API_KEY_INVALID")
            if fake.answer_next:
                planned = fake.answer_next.pop(0)
                if isinstance(planned, tuple):
                    return planned
                return planned, {"error": {"code": planned, "message": "Backend Error"}}
            if fake.disabled:
                return _error(403, "YouTube Data API v3 has not been used in project 1234 before or it is disabled. "
                                   "Enable it by visiting https://console.developers.google.com/ then retry.",
                              "accessNotConfigured", "usageLimits", "PERMISSION_DENIED", "SERVICE_DISABLED")
            if fake.restricted:
                return _error(403, "Requests to this API youtube method youtube.api.v3.V3DataChannelService.List are "
                                   "blocked.", "forbidden", "global", "PERMISSION_DENIED", "API_KEY_SERVICE_BLOCKED")
            if fake.units_left is not None:
                if fake.units_left < COSTS[resource]:
                    return _error(403, 'The request cannot be completed because you have exceeded your <a href="/youtube'
                                       '/v3/getting-started#quota">quota</a>.', "quotaExceeded", "youtube.quota")
                fake.units_left -= COSTS[resource]
            return getattr(self, f"_{resource}")(query)

        def _channels(self, query: dict[str, str]) -> tuple[int, dict[str, Any]]:
            if "id" in query:
                found = [i for i in query["id"].split(",") if i in fake.channels]
            elif "forHandle" in query:
                wanted = query["forHandle"].lstrip("@").lower()
                found = [i for i, c in fake.channels.items() if c["handle"].lstrip("@").lower() == wanted]
            elif "forUsername" in query:
                found = [i for i, c in fake.channels.items() if c["username"].lower() == query["forUsername"].lower()]
            else:
                return _error(400, "No filter selected. Expected one of: forHandle, forUsername, id", "missingRequiredParameter")
            body: dict[str, Any] = {"kind": "youtube#channelListResponse",
                                    "pageInfo": {"totalResults": len(found), "resultsPerPage": 5}}
            if found:   # like the real API: no "items" at all when nothing matches
                body["items"] = [fake._channel_item(i) for i in found]
            return 200, body

        def _search(self, query: dict[str, str]) -> tuple[int, dict[str, Any]]:
            words = query.get("q", "").lower()
            found = [i for i, c in fake.channels.items() if words and words in c["title"].lower()]
            found = found[:int(query.get("maxResults", "5"))]
            return 200, {"kind": "youtube#searchListResponse", "items": [
                {"kind": "youtube#searchResult", "id": {"kind": "youtube#channel", "channelId": i},
                 "snippet": {"channelId": i, "title": fake.channels[i]["title"], "description": ""}} for i in found]}

        def _playlistItems(self, query: dict[str, str]) -> tuple[int, dict[str, Any]]:  # noqa: N802
            owner = next((i for i in fake.channels if fake._uploads_id(i) == query.get("playlistId")), None)
            if owner is None:
                return _error(404, "The playlist identified with the request's <code>playlistId</code> parameter "
                                   "cannot be found.", "playlistNotFound", "youtube.playlistItem", "NOT_FOUND")
            ids = fake.uploads.get(owner, [])[:int(query.get("maxResults", "5"))]
            return 200, {"kind": "youtube#playlistItemListResponse", "items": [
                {"kind": "youtube#playlistItem", "contentDetails": {"videoId": i,
                                                                   "videoPublishedAt": fake.videos[i]["published"]}}
                for i in ids if i in fake.videos]}

        def _videos(self, query: dict[str, str]) -> tuple[int, dict[str, Any]]:
            found = [i for i in query.get("id", "").split(",") if i in fake.videos]
            body: dict[str, Any] = {"kind": "youtube#videoListResponse",
                                    "pageInfo": {"totalResults": len(found), "resultsPerPage": len(found)}}
            if found:
                body["items"] = [fake._video_item(i) for i in found]
            return 200, body

        def _commentThreads(self, query: dict[str, str]) -> tuple[int, dict[str, Any]]:  # noqa: N802
            video_id = query.get("videoId", "")
            if video_id not in fake.videos:
                return _error(404, "The video identified by the <code>videoId</code> parameter could not be found.",
                              "videoNotFound", "youtube.commentThread", "NOT_FOUND")
            if video_id in fake.comments_off:
                return _error(403, 'The video identified by the <code><a href="/youtube/v3/docs/commentThreads/list#'
                                   'videoId">videoId</a></code> parameter has disabled comments.', "commentsDisabled",
                              "youtube.commentThread", "PERMISSION_DENIED")
            rows = fake.comments.get(video_id, [])[:int(query.get("maxResults", "20"))]
            return 200, {"kind": "youtube#commentThreadListResponse", "items": [
                {"kind": "youtube#commentThread", "snippet": {"totalReplyCount": 0, "topLevelComment": {"snippet": {
                    "textDisplay": text, "textOriginal": text, "likeCount": likes, "authorDisplayName": "viewer",
                    "publishedAt": "2026-11-21T10:00:00Z"}}}} for text, likes in rows]}

    return Handler


@pytest.fixture()
def youtube(monkeypatch):  # noqa: ANN001, ANN201
    """The stand-in, running on this PC, with Creator Discovery pointed at it."""
    from app.discovery import youtube_api

    fake = FakeYouTubeData()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(fake))
    server.daemon_threads = True
    fake.base = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(youtube_api, "OVERRIDE", fake.url)
    yield fake
    server.shutdown()
    server.server_close()
