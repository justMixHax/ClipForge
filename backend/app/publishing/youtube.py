"""YouTube Shorts through the YouTube Data API v3.

Sign-in: Google's flow for installed apps (loopback address on this PC, PKCE). Upload: the resumable protocol, so
an upload cut off half-way continues where it stopped, and a retried post resumes the same upload instead of
making a second video. Confirmation: videos.list. A vertical video of up to three minutes becomes a Short.
Scheduling uses YouTube's own publishAt, so a scheduled Short goes live on time even if this PC is off.
"""
from __future__ import annotations

import json
import time
from datetime import timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from .base import (CancelFn, Draft, Http, Outcome, Profile, ProgressFn, PublishError, RememberFn, Tokens, file_part,
                   pause)
from .oauth import challenge_s256

SCOPES = ("https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly")
QUOTA_REASONS = {"quotaExceeded", "uploadLimitExceeded", "dailyLimitExceeded", "rateLimitExceeded",
                 "userRateLimitExceeded"}
RECONNECT_HINT = ("Reconnect YouTube under Publishing → Accounts. If Google ends the sign-in every 7 days, set your "
                  "Google app's publishing status to In production (Google Auth Platform → Audience).")


def clean_text(text: str) -> str:
    """YouTube refuses < and > in titles and descriptions."""
    return (text or "").replace("<", "").replace(">", "").strip()


def cut_bytes(text: str, limit: int) -> str:
    data = text.encode("utf-8")
    return text if len(data) <= limit else data[:limit].decode("utf-8", "ignore").rstrip()


class SessionGone(PublishError):
    """The resumable upload session expired; a new one must be started."""


class YouTubeHttp(Http):
    def error_for(self, response: httpx.Response) -> PublishError:
        reason = ""
        try:
            errors = (response.json().get("error") or {}).get("errors") or []
            reason = str(errors[0].get("reason", "")) if errors else ""
        except (ValueError, AttributeError):
            pass
        if reason in QUOTA_REASONS:
            return PublishError("YouTube's daily upload limit for your Google project is used up.",
                                hint="It resets at midnight Pacific time. ClipForge tries again later.", retry=True)
        if reason == "youtubeSignupRequired":
            return PublishError("This Google account has no YouTube channel yet.",
                                hint="Create a channel on YouTube, then connect again.")
        return super().error_for(response)


class YouTube:
    platform = "youtube"
    label = "YouTube"
    ENDPOINTS = {"auth": "https://accounts.google.com/o/oauth2/v2/auth", "token": "https://oauth2.googleapis.com/token",
                 "revoke": "https://oauth2.googleapis.com/revoke", "api": "https://www.googleapis.com/youtube/v3",
                 "upload": "https://www.googleapis.com/upload/youtube/v3/videos"}
    RETRY_WAITS: tuple[float, ...] = (5.0, 15.0, 45.0)
    POLL_WAITS: tuple[float, ...] = (5.0, 10.0, 20.0)

    def __init__(self, client_id: str, client_secret: str, *, endpoints: dict[str, str] | None = None,
                 http: Http | None = None) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.urls = {**self.ENDPOINTS, **(endpoints or {})}
        self.http = http or YouTubeHttp(self.label)

    # ------------------------------------------------------------------------------------------- sign-in
    def authorize_url(self, redirect_uri: str, state: str, verifier: str) -> str:
        return self.urls["auth"] + "?" + urlencode({
            "client_id": self.client_id, "redirect_uri": redirect_uri, "response_type": "code",
            "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent", "state": state,
            "code_challenge": challenge_s256(verifier), "code_challenge_method": "S256"})

    def _tokens(self, data: dict[str, Any], previous: Tokens | None = None) -> Tokens:
        access = str(data.get("access_token") or "")
        if not access:
            raise PublishError("Google did not send back a sign-in token.")
        return Tokens(access_token=access,
                      refresh_token=str(data.get("refresh_token") or (previous.refresh_token if previous else "")),
                      expires_at=time.time() + float(data.get("expires_in") or 3600),
                      scopes=str(data.get("scope") or "").split() or (previous.scopes if previous else []))

    def exchange(self, code: str, redirect_uri: str, verifier: str) -> Tokens:
        data = self.http.json("POST", self.urls["token"], expect=(200,), data={
            "code": code, "client_id": self.client_id, "client_secret": self.client_secret,
            "redirect_uri": redirect_uri, "grant_type": "authorization_code", "code_verifier": verifier})
        return self._tokens(data)

    def refresh(self, tokens: Tokens) -> Tokens:
        if not tokens.refresh_token:
            raise PublishError("This YouTube sign-in cannot be renewed.", hint=RECONNECT_HINT, reconnect=True)
        try:
            data = self.http.json("POST", self.urls["token"], expect=(200,), data={
                "client_id": self.client_id, "client_secret": self.client_secret,
                "refresh_token": tokens.refresh_token, "grant_type": "refresh_token"})
        except PublishError as exc:
            if exc.retry:
                raise
            raise PublishError("Google ended this YouTube sign-in.", hint=RECONNECT_HINT, reconnect=True) from exc
        return self._tokens(data, previous=tokens)

    def revoke(self, tokens: Tokens) -> None:
        try:
            self.http.request("POST", self.urls["revoke"], expect=(200, 400),
                              data={"token": tokens.refresh_token or tokens.access_token})
        except PublishError:
            pass  # best effort: the stored tokens are deleted either way

    def profile(self, tokens: Tokens) -> Profile:
        data = self.http.json("GET", f"{self.urls['api']}/channels", params={"part": "snippet", "mine": "true"},
                              headers=self._auth(tokens))
        items = data.get("items") or []
        if not items:
            raise PublishError("This Google account has no YouTube channel yet.",
                               hint="Create a channel on YouTube, then connect again.")
        channel = items[0]
        snippet = channel.get("snippet") or {}
        return Profile(external_id=str(channel.get("id", "")), name=str(snippet.get("title") or "YouTube channel"),
                       handle=str(snippet.get("customUrl") or ""))

    @staticmethod
    def _auth(tokens: Tokens) -> dict[str, str]:
        return {"Authorization": f"Bearer {tokens.access_token}"}

    # ------------------------------------------------------------------------------------------- posting
    def video_body(self, draft: Draft) -> dict[str, Any]:
        title = " ".join(clean_text(draft.title).split())[:100].strip()
        if not title:
            raise PublishError("YouTube needs a title: up to 100 characters, without < or >.")
        tags_line = " ".join(t for t in draft.hashtags if t.strip())
        description = clean_text(draft.caption)
        if tags_line:
            description = f"{description}\n\n{clean_text(tags_line)}" if description else clean_text(tags_line)
        tags: list[str] = []
        total = 0
        for tag in draft.hashtags:
            word = clean_text(tag.lstrip("#"))
            cost = len(word) + (2 if " " in word else 0) + (1 if tags else 0)
            if not word or total + cost > 500:
                continue
            tags.append(word)
            total += cost
        privacy = draft.privacy if draft.privacy in ("public", "unlisted", "private") else "public"
        status: dict[str, Any] = {"privacyStatus": privacy,
                                  "selfDeclaredMadeForKids": bool(draft.options.get("made_for_kids", False)),
                                  "containsSyntheticMedia": bool(draft.options.get("synthetic_media", False))}
        if draft.publish_at is not None:  # YouTube publishes it itself: private until then
            status["privacyStatus"] = "private"
            status["publishAt"] = draft.publish_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"snippet": {"title": title, "description": cut_bytes(description, 5000), "tags": tags,
                            "categoryId": str(draft.options.get("category_id") or "24")},
                "status": status}

    def publish(self, tokens: Tokens, draft: Draft, *, account: str, state: dict[str, Any], remember: RememberFn,
                on_progress: ProgressFn, should_cancel: CancelFn) -> Outcome:
        body = self.video_body(draft)
        size = draft.size
        if not state.get("upload_session"):
            response = self.http.request(
                "POST", self.urls["upload"], expect=(200,),
                params={"uploadType": "resumable", "part": "snippet,status",
                        "notifySubscribers": "true" if draft.options.get("notify_subscribers", True) else "false"},
                headers={**self._auth(tokens), "Content-Type": "application/json; charset=UTF-8",
                         "X-Upload-Content-Length": str(size), "X-Upload-Content-Type": "video/mp4"},
                content=json.dumps(body).encode("utf-8"))
            location = response.headers.get("location")
            if not location:
                raise PublishError("YouTube did not start the upload.", retry=True)
            state["upload_session"] = location
            state.pop("upload_started", None)
            remember(state)
        try:
            video = self._send(tokens, draft, size, state, remember, on_progress, should_cancel)
        except SessionGone:
            for key in ("upload_session", "upload_started"):
                state.pop(key, None)
            remember(state)
            raise
        state["video_id"] = str(video.get("id") or "")
        remember(state)
        outcome = self.judge(video)
        for wait in self.POLL_WAITS:
            if outcome.status != "processing":
                break
            pause(wait, should_cancel)
            outcome = self.check(tokens, outcome.external_id, account=account, state=state)
        return outcome

    def _send(self, tokens: Tokens, draft: Draft, size: int, state: dict[str, Any], remember: RememberFn,
              on_progress: ProgressFn, should_cancel: CancelFn) -> dict[str, Any]:
        session = state["upload_session"]
        ask_first = bool(state.get("upload_started"))
        for attempt in range(len(self.RETRY_WAITS) + 1):
            try:
                offset = 0
                if ask_first:
                    received = self._received(tokens, session, size)
                    if isinstance(received, dict):
                        return received  # it had already arrived in full
                    offset = received
                if not state.get("upload_started"):
                    state["upload_started"] = True
                    remember(state)
                sent = [offset]

                def progress(n: int) -> None:
                    sent[0] += n
                    on_progress(min(1.0, sent[0] / size))

                headers = {**self._auth(tokens), "Content-Type": "video/mp4", "Content-Length": str(size - offset)}
                if offset:
                    headers["Content-Range"] = f"bytes {offset}-{size - 1}/{size}"
                response = self.http.request("PUT", session, expect=(200, 201), headers=headers,
                                             content=file_part(draft.file, offset, size, progress, should_cancel))
                return self._video(response)
            except SessionGone:
                raise
            except PublishError as exc:
                if not exc.retry or attempt >= len(self.RETRY_WAITS):
                    raise
                ask_first = True
                pause(self.RETRY_WAITS[attempt], should_cancel)
        raise PublishError("The upload to YouTube did not finish.", retry=True)  # not reached

    def _received(self, tokens: Tokens, session: str, size: int) -> int | dict[str, Any]:
        """How much of the file YouTube has: the next byte to send, or the video when it is all there."""
        response = self.http.request("PUT", session, expect=(200, 201, 308, 404), content=b"",
                                     headers={**self._auth(tokens), "Content-Length": "0",
                                              "Content-Range": f"bytes */{size}"})
        if response.status_code == 404:
            raise SessionGone("YouTube's upload session ran out, so the upload has to start again.", retry=True)
        if response.status_code in (200, 201):
            return self._video(response)
        have = response.headers.get("range", "")
        return int(have.rsplit("-", 1)[1]) + 1 if "-" in have else 0

    def _video(self, response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise PublishError("YouTube finished the upload but its answer could not be read.") from exc
        return data if isinstance(data, dict) else {}

    def judge(self, video: dict[str, Any]) -> Outcome:
        vid = str(video.get("id") or "")
        if not vid:
            raise PublishError("YouTube finished the upload but sent back no video id.")
        url = f"https://youtube.com/shorts/{vid}"
        status = video.get("status") or {}
        upload = str(status.get("uploadStatus") or "uploaded")
        if upload in ("failed", "rejected", "deleted"):
            reason = status.get("rejectionReason") or status.get("failureReason") or ""
            raise PublishError(f"YouTube {upload} the video" + (f" ({reason})" if reason else "") + ".",
                               hint="YouTube Studio has the details.")
        if status.get("publishAt"):
            return Outcome("scheduled_on_platform", vid, url, f"Uploaded. YouTube publishes it at {status['publishAt']}.")
        if status.get("privacyStatus") == "private":
            return Outcome("private", vid, url, "Uploaded as private: only you can see it until you change that in "
                                                "YouTube Studio.")
        if upload == "processed":
            return Outcome("published", vid, url, f"Live on YouTube ({status.get('privacyStatus') or 'public'}).")
        return Outcome("processing", vid, url, "Uploaded. YouTube is still processing it.", check_again_in=60.0)

    def check(self, tokens: Tokens, external_id: str, *, account: str, state: dict[str, Any],
              remember: RememberFn | None = None) -> Outcome:
        data = self.http.json("GET", f"{self.urls['api']}/videos", headers=self._auth(tokens),
                              params={"part": "status,processingDetails", "id": external_id})
        items = data.get("items") or []
        if not items:
            raise PublishError("YouTube no longer has this video. It may have been deleted.")
        return self.judge(items[0])
