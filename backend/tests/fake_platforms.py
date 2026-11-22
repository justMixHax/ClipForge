"""A local stand-in for the YouTube, TikTok and Facebook/Instagram APIs, built from their documentation, so the
adapters can be tested end to end without real accounts. Every request is recorded for the tests to check.

It only knows the calls ClipForge makes. Knobs on the instance make it misbehave the way the real services can:
refuse a sign-in, drop an upload half-way, keep processing for a while, report a failure.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest


@dataclass
class Seen:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: bytes

    @property
    def form(self) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(self.body.decode("utf-8", "replace")).items()}

    @property
    def json(self) -> Any:
        return json.loads(self.body or b"{}")


@dataclass
class FakePlatforms:
    base: str = ""
    seen: list[Seen] = field(default_factory=list)
    # --- Google / YouTube
    google_token_status: int = 200
    channels: list[dict[str, Any]] = field(default_factory=lambda: [
        {"id": "UC_test_channel", "snippet": {"title": "Forge Clips", "customUrl": "@forgeclips"}}])
    youtube_break_after: int | None = None   # drop the first upload attempt after this many bytes
    youtube_put_status: int | None = None    # answer every upload with this status instead
    youtube_upload_status: str = "processed"  # what videos.list reports
    youtube_init_error: tuple[int, str] | None = None  # (status, reason) for starting an upload
    youtube_sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    youtube_videos: dict[str, dict[str, Any]] = field(default_factory=dict)
    # --- TikTok
    tiktok_statuses: list[str] = field(default_factory=lambda: ["PROCESSING_UPLOAD", "SEND_TO_USER_INBOX"])
    tiktok_init_error: str = "ok"
    tiktok_parts: dict[str, bytearray] = field(default_factory=dict)
    tiktok_refresh_count: int = 0
    # --- Facebook / Instagram
    ig_statuses: list[str] = field(default_factory=lambda: ["IN_PROGRESS", "FINISHED"])
    ig_uploads: dict[str, bytes] = field(default_factory=dict)
    ig_published: list[str] = field(default_factory=list)
    pages: list[dict[str, Any]] = field(default_factory=lambda: [
        {"id": "page1", "name": "Forge Page",
         "instagram_business_account": {"id": "17841400000000001", "username": "forge.clips", "name": "Forge"}},
        {"id": "page2", "name": "Page without Instagram"}])

    def calls(self, method: str, path_start: str) -> list[Seen]:
        return [s for s in self.seen if s.method == method and s.path.startswith(path_start)]

    def endpoints(self) -> dict[str, dict[str, str]]:
        b = self.base
        return {
            "youtube": {"auth": f"{b}/google/auth", "token": f"{b}/google/token", "revoke": f"{b}/google/revoke",
                        "api": f"{b}/youtube/v3", "upload": f"{b}/upload/youtube/v3/videos"},
            "tiktok": {"auth": f"{b}/tiktok/auth/", "token": f"{b}/v2/oauth/token/", "revoke": f"{b}/v2/oauth/revoke/",
                       "api": f"{b}/v2"},
            "instagram": {"graph": f"{b}/v27.0", "rupload": f"{b}/rupload"},
        }


def _handler(fake: FakePlatforms):  # noqa: C901  (one small fake per platform, kept together)
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: Any) -> None:
            pass

        def _read(self) -> bytes:
            if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                out = bytearray()
                while True:
                    size = int(self.rfile.readline().strip() or b"0", 16)
                    if size == 0:
                        self.rfile.readline()
                        return bytes(out)
                    out += self.rfile.read(size)
                    self.rfile.readline()
            return self.rfile.read(int(self.headers.get("Content-Length") or 0))

        def _send(self, status: int, body: Any = None, headers: dict[str, str] | None = None) -> None:
            data = b"" if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode("utf-8"))
            self.send_response(status)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            if body is not None and not isinstance(body, bytes):
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _handle(self, method: str) -> None:
            parts = urlsplit(self.path)
            path = parts.path
            query = {k: v[0] for k, v in parse_qs(parts.query).items()}
            # the YouTube upload is read below, in pieces, so a break half-way can be simulated
            streaming = method == "PUT" and path.startswith("/upload-session/")
            body = b"" if streaming else self._read()
            seen = Seen(method, path, query, {k.lower(): v for k, v in self.headers.items()}, body)
            fake.seen.append(seen)
            bearer = self.headers.get("Authorization", "")

            # ---------------------------------------------------------------- Google / YouTube
            if path == "/google/token":
                form = seen.form
                if fake.google_token_status != 200:
                    return self._send(fake.google_token_status, {"error": "invalid_grant",
                                                                 "error_description": "Token has been expired or revoked."})
                if form.get("grant_type") == "authorization_code":
                    return self._send(200, {"access_token": "g-access-1", "refresh_token": "g-refresh-1",
                                            "expires_in": 3599, "scope": " ".join([
                                                "https://www.googleapis.com/auth/youtube.upload",
                                                "https://www.googleapis.com/auth/youtube.readonly"]),
                                            "token_type": "Bearer"})
                return self._send(200, {"access_token": "g-access-2", "expires_in": 3599, "token_type": "Bearer"})
            if path == "/google/revoke":
                return self._send(200, {})
            if path == "/youtube/v3/channels":
                if not bearer.startswith("Bearer g-access"):
                    return self._send(401, {"error": {"code": 401, "message": "Invalid Credentials"}})
                return self._send(200, {"items": fake.channels})
            if path == "/upload/youtube/v3/videos" and method == "POST":
                if not bearer.startswith("Bearer g-access"):
                    return self._send(401, {"error": {"code": 401, "message": "Invalid Credentials"}})
                if fake.youtube_init_error is not None:
                    status, reason = fake.youtube_init_error
                    return self._send(status, {"error": {"code": status, "message": "Request refused by the fake.",
                                                         "errors": [{"reason": reason}]}})
                key = f"s{len(fake.youtube_sessions) + 1}"
                fake.youtube_sessions[key] = {"size": int(self.headers.get("X-Upload-Content-Length") or 0),
                                              "data": bytearray(), "body": seen.json, "query": query, "puts": 0}
                return self._send(200, None, {"Location": f"{fake.base}/upload-session/{key}"})
            if streaming:
                key = path.rsplit("/", 1)[-1]
                session = fake.youtube_sessions.get(key)
                if session is None:
                    self._read()
                    return self._send(404, {"error": {"code": 404, "message": "Not found"}})
                session["puts"] += 1
                length = int(self.headers.get("Content-Length") or 0)
                content_range = self.headers.get("Content-Range", "")
                if length == 0 and content_range.startswith("bytes */"):
                    got = len(session["data"])
                    if got >= session["size"]:
                        return self._send(200, session["video"])
                    return self._send(308, None, {"Range": f"bytes=0-{got - 1}"} if got else None)
                if fake.youtube_put_status is not None:
                    self.rfile.read(length)
                    return self._send(fake.youtube_put_status, {"error": {"code": fake.youtube_put_status,
                                                                          "message": "Backend Error",
                                                                          "errors": [{"reason": "backendError"}]}})
                start = int(content_range.split(" ")[1].split("-")[0]) if content_range.startswith("bytes ") and "*" not in content_range else 0
                if start != len(session["data"]):
                    self.rfile.read(length)
                    return self._send(400, {"error": {"message": f"wrong offset {start}"}})
                if fake.youtube_break_after is not None and session["puts"] == 1:
                    session["data"] += self.rfile.read(min(length, fake.youtube_break_after))
                    self.close_connection = True
                    self.connection.shutdown(2)
                    return None
                session["data"] += self.rfile.read(length)
                body_in = session["body"]
                status = {"uploadStatus": "uploaded", "privacyStatus": body_in["status"]["privacyStatus"]}
                if body_in["status"].get("publishAt"):
                    status["publishAt"] = body_in["status"]["publishAt"]
                video = {"id": f"vid{key}", "snippet": body_in["snippet"], "status": status}
                session["video"] = video
                fake.youtube_videos[video["id"]] = video
                return self._send(201, video)
            if path == "/youtube/v3/videos" and method == "GET":
                video = fake.youtube_videos.get(query.get("id", ""))
                if video is None:
                    return self._send(200, {"items": []})
                status = dict(video["status"])
                status["uploadStatus"] = fake.youtube_upload_status
                if fake.youtube_upload_status == "rejected":
                    status["rejectionReason"] = "copyright"
                return self._send(200, {"items": [{"id": video["id"], "status": status}]})

            # ---------------------------------------------------------------- TikTok
            if path == "/v2/oauth/token/":
                form = seen.form
                if form.get("grant_type") == "refresh_token":
                    fake.tiktok_refresh_count += 1
                    n = fake.tiktok_refresh_count + 1
                    return self._send(200, {"access_token": f"t-access-{n}", "expires_in": 7200,
                                            "refresh_token": f"t-refresh-{n}", "refresh_expires_in": 15552000,
                                            "open_id": "tt-open-1", "scope": "user.info.basic,video.upload",
                                            "token_type": "Bearer"})
                return self._send(200, {"access_token": "t-access-1", "expires_in": 7200, "refresh_token": "t-refresh-1",
                                        "refresh_expires_in": 15552000, "open_id": "tt-open-1",
                                        "scope": "user.info.basic,video.upload", "token_type": "Bearer"})
            if path == "/v2/oauth/revoke/":
                return self._send(200, {})
            if path == "/v2/user/info/":
                return self._send(200, {"data": {"user": {"open_id": "tt-open-1", "display_name": "Forge TT",
                                                          "avatar_url": "https://example.invalid/a.jpg"}},
                                        "error": {"code": "ok", "message": ""}})
            if path == "/v2/post/publish/inbox/video/init/":
                if fake.tiktok_init_error != "ok":
                    return self._send(403, {"data": {}, "error": {"code": fake.tiktok_init_error,
                                                                 "message": "refused by the fake"}})
                info = seen.json["source_info"]
                key = f"p{len(fake.tiktok_parts) + 1}"
                fake.tiktok_parts[key] = bytearray()
                fake.tiktok_parts[key + ":info"] = bytearray(json.dumps(info).encode())
                return self._send(200, {"data": {"publish_id": f"v_inbox_file~{key}",
                                                 "upload_url": f"{fake.base}/tiktok-upload/{key}?sig=secret"},
                                        "error": {"code": "ok", "message": ""}})
            if path.startswith("/tiktok-upload/"):
                key = path.rsplit("/", 1)[-1]
                info = json.loads(bytes(fake.tiktok_parts[key + ":info"]))
                first, last = map(int, self.headers["Content-Range"].split(" ")[1].split("/")[0].split("-"))
                if first != len(fake.tiktok_parts[key]) or len(body) != last - first + 1:
                    return self._send(416, None, {"Content-Range": f"bytes 0-{len(fake.tiktok_parts[key]) - 1}"})
                fake.tiktok_parts[key] += body
                return self._send(201 if len(fake.tiktok_parts[key]) >= info["video_size"] else 206, None)
            if path == "/v2/post/publish/status/fetch/":
                status = fake.tiktok_statuses.pop(0) if len(fake.tiktok_statuses) > 1 else fake.tiktok_statuses[0]
                data: dict[str, Any] = {"status": status}
                if status == "FAILED":
                    data["fail_reason"] = "duration_check_failed"
                if status == "PUBLISH_COMPLETE":
                    data["publicaly_available_post_id"] = [7300000000000000001]
                return self._send(200, {"data": data, "error": {"code": "ok", "message": ""}})

            # ---------------------------------------------------------------- Facebook / Instagram
            if path == "/v27.0/oauth/access_token":
                form = seen.form or query
                if form.get("fb_exchange_token") != "short-lived-token":
                    return self._send(400, {"error": {"message": "Invalid OAuth access token.", "code": 190}})
                return self._send(200, {"access_token": "fb-long-1", "token_type": "bearer", "expires_in": 5183944})
            if path == "/v27.0/me/accounts":
                if bearer != "OAuth fb-long-1":
                    return self._send(400, {"error": {"message": "Invalid OAuth access token.", "code": 190}})
                return self._send(200, {"data": fake.pages})
            if path == "/v27.0/me/permissions" and method == "DELETE":
                return self._send(200, {"success": True})
            if path.endswith("/media") and method == "POST":
                key = f"c{len(fake.ig_uploads) + 1}"
                fake.ig_uploads[key] = b""
                return self._send(200, {"id": key, "uri": f"{fake.base}/rupload/{key}"})
            if path.startswith("/rupload/"):
                key = path.rsplit("/", 1)[-1]
                if self.headers.get("Authorization") != "OAuth fb-long-1" or self.headers.get("offset") != "0" \
                        or int(self.headers.get("file_size") or -1) != len(body):
                    return self._send(400, {"success": False, "message": "bad upload headers"})
                fake.ig_uploads[key] = body
                return self._send(200, {"success": True, "message": "Upload successful."})
            if path.endswith("/media_publish"):
                creation = seen.form.get("creation_id") or query.get("creation_id", "")
                fake.ig_published.append(creation)
                return self._send(200, {"id": f"media-{creation}"})
            if path.startswith("/v27.0/media-"):
                return self._send(200, {"id": path.rsplit("/", 1)[-1],
                                        "permalink": f"https://www.instagram.com/reel/{path.rsplit('/', 1)[-1]}/"})
            if path.startswith("/v27.0/c"):
                status = fake.ig_statuses.pop(0) if len(fake.ig_statuses) > 1 else fake.ig_statuses[0]
                return self._send(200, {"id": path.rsplit("/", 1)[-1], "status_code": status,
                                        "status": "Error: 2207026" if status == "ERROR" else status})
            return self._send(404, {"error": {"message": f"the fake does not know {method} {path}"}})

        def do_GET(self) -> None:  # noqa: N802
            self._handle("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._handle("POST")

        def do_PUT(self) -> None:  # noqa: N802
            self._handle("PUT")

        def do_DELETE(self) -> None:  # noqa: N802
            self._handle("DELETE")

    return Handler


@pytest.fixture()
def platforms(monkeypatch):  # noqa: ANN001, ANN201
    """The fake platforms, with ClipForge's adapters pointed at them and waits shortened to nothing."""
    from app.publishing import instagram, store, tiktok, youtube

    fake = FakePlatforms()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(fake))
    fake.base = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(store, "OVERRIDES", fake.endpoints())
    monkeypatch.setattr(youtube.YouTube, "RETRY_WAITS", (0.0, 0.0, 0.0))
    monkeypatch.setattr(youtube.YouTube, "POLL_WAITS", (0.0, 0.0, 0.0))
    monkeypatch.setattr(tiktok.TikTok, "POLL_WAITS", (0.0,) * 6)
    monkeypatch.setattr(instagram.Instagram, "POLL_WAITS", (0.0,) * 6)
    yield fake
    server.shutdown()
    server.server_close()
