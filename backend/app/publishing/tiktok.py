"""TikTok through the Content Posting API, as drafts.

Sign-in: Login Kit for desktop (loopback address on this PC, PKCE with the hex challenge TikTok's documentation asks
for). Posting: "upload to inbox". The clip arrives in your TikTok inbox and you finish the post in the TikTok app
(caption, sound, who can see it). Direct posting is not offered: TikTok's rules for it exclude tools for posting
to your own accounts, and apps it has not audited may only post privately to private accounts.
"""
from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlencode

import httpx

from .base import (CancelFn, Draft, Http, Outcome, Profile, ProgressFn, PublishError, RememberFn, Tokens, file_part,
                   pause)
from .oauth import challenge_hex

SCOPES = ("user.info.basic", "video.upload")
MB = 1024 * 1024
RECONNECT_HINT = "Reconnect TikTok under Publishing → Accounts."
FAIL_REASONS = {
    "file_format_check_failed": "TikTok could not read the video file's format.",
    "duration_check_failed": "TikTok refused the video's length.",
    "frame_rate_check_failed": "TikTok refused the video's frame rate.",
    "picture_size_check_failed": "TikTok refused the video's picture size.",
    "internal": "TikTok had an internal problem with the upload.",
    "publish_cancelled": "The post was cancelled in TikTok.",
    "auth_removed": "ClipForge's access to this TikTok account was removed.",
    "spam_risk_too_many_posts": "TikTok's daily post limit for this account is reached.",
    "spam_risk_user_banned_from_posting": "TikTok does not let this account post right now.",
    "spam_risk_text": "TikTok flagged the text of the post.",
    "spam_risk": "TikTok flagged the post.",
}
# code: (message, hint, retry, reconnect)
ERROR_CODES: dict[str, tuple[str, str, bool, bool]] = {
    "spam_risk_too_many_pending_share": ("TikTok allows at most 5 drafts waiting in your inbox in 24 hours.",
                                         "Post or delete the waiting drafts in the TikTok app, then retry.", False,
                                         False),
    "access_token_invalid": ("TikTok no longer accepts this sign-in.", RECONNECT_HINT, False, True),
    "scope_not_authorized": ("This TikTok sign-in does not allow uploading videos.",
                             "Make sure your TikTok app has the video.upload scope, then reconnect.", False, True),
    "rate_limit_exceeded": ("TikTok says there have been too many requests.", "ClipForge tries again later.", True,
                            False),
    "spam_risk_user_banned_from_posting": ("TikTok does not let this account post right now.", "", False, False),
    "spam_risk_too_many_posts": ("TikTok's daily post limit for this account is reached.", "Try again tomorrow.",
                                 False, False),
}


def plan_chunks(size: int, single_max: int = 64 * MB, chunk: int = 32 * MB) -> tuple[int, int]:
    """(chunk size, number of chunks) by TikTok's rules: up to 64 MB in one piece; above that 5-64 MB pieces,
    the count rounded down, the last piece taking what is left (under 128 MB)."""
    if size <= single_max:
        return size, 1
    return chunk, max(1, size // chunk)


class TikTokHttp(Http):
    def error_for(self, response: httpx.Response) -> PublishError:
        try:
            error = response.json().get("error") or {}
        except (ValueError, AttributeError):
            error = {}
        code = str(error.get("code") or "") if isinstance(error, dict) else ""
        if code in ERROR_CODES:
            message, hint, retry, reconnect = ERROR_CODES[code]
            return PublishError(message, hint=hint, retry=retry, reconnect=reconnect)
        return super().error_for(response)


class TikTok:
    platform = "tiktok"
    label = "TikTok"
    ENDPOINTS = {"auth": "https://www.tiktok.com/v2/auth/authorize/", "token": "https://open.tiktokapis.com/v2/oauth/token/",
                 "revoke": "https://open.tiktokapis.com/v2/oauth/revoke/", "api": "https://open.tiktokapis.com/v2"}
    SINGLE_MAX = 64 * MB
    CHUNK = 32 * MB
    RETRY_WAITS: tuple[float, ...] = (5.0, 15.0, 45.0)
    POLL_WAITS: tuple[float, ...] = (3.0, 5.0, 8.0, 12.0, 20.0, 30.0)  # status checks stay under 30 a minute

    def __init__(self, client_key: str, client_secret: str, *, endpoints: dict[str, str] | None = None,
                 http: Http | None = None) -> None:
        self.client_key = client_key
        self.client_secret = client_secret
        self.urls = {**self.ENDPOINTS, **(endpoints or {})}
        self.http = http or TikTokHttp(self.label)

    # ------------------------------------------------------------------------------------------- sign-in
    def authorize_url(self, redirect_uri: str, state: str, verifier: str) -> str:
        return self.urls["auth"] + "?" + urlencode({
            "client_key": self.client_key, "scope": ",".join(SCOPES), "response_type": "code",
            "redirect_uri": redirect_uri, "state": state, "code_challenge": challenge_hex(verifier),
            "code_challenge_method": "S256"})

    def _tokens(self, data: dict[str, Any], reconnect: bool = False) -> Tokens:
        access = str(data.get("access_token") or "")
        if not access:
            said = data.get("error_description") or data.get("error") or "no token in the answer"
            raise PublishError(f"TikTok did not accept the sign-in ({said}).", hint=RECONNECT_HINT, reconnect=reconnect)
        now = time.time()
        return Tokens(access_token=access, refresh_token=str(data.get("refresh_token") or ""),
                      expires_at=now + float(data.get("expires_in") or 7200),
                      refresh_expires_at=now + float(data.get("refresh_expires_in") or 0) if data.get("refresh_expires_in") else 0.0,
                      scopes=[s for s in str(data.get("scope") or "").split(",") if s])

    def exchange(self, code: str, redirect_uri: str, verifier: str) -> Tokens:
        data = self.http.json("POST", self.urls["token"], expect=(200,), data={
            "client_key": self.client_key, "client_secret": self.client_secret, "code": code,
            "grant_type": "authorization_code", "redirect_uri": redirect_uri, "code_verifier": verifier})
        return self._tokens(data)

    def refresh(self, tokens: Tokens) -> Tokens:
        if not tokens.refresh_token:
            raise PublishError("This TikTok sign-in cannot be renewed.", hint=RECONNECT_HINT, reconnect=True)
        try:
            data = self.http.json("POST", self.urls["token"], expect=(200,), data={
                "client_key": self.client_key, "client_secret": self.client_secret,
                "grant_type": "refresh_token", "refresh_token": tokens.refresh_token})
        except PublishError as exc:
            if exc.retry:
                raise
            raise PublishError("TikTok ended this sign-in.", hint=RECONNECT_HINT, reconnect=True) from exc
        return self._tokens(data, reconnect=True)  # TikTok sends a new refresh token: the old one stops working

    def revoke(self, tokens: Tokens) -> None:
        try:
            self.http.request("POST", self.urls["revoke"], expect=(200, 400, 401), data={
                "client_key": self.client_key, "client_secret": self.client_secret, "token": tokens.access_token})
        except PublishError:
            pass

    def _api(self, path: str, tokens: Tokens, body: dict[str, Any] | None = None,
             params: dict[str, str] | None = None, method: str = "POST") -> dict[str, Any]:
        data = self.http.json(method, f"{self.urls['api']}{path}", params=params, json=body,
                              headers={"Authorization": f"Bearer {tokens.access_token}",
                                       "Content-Type": "application/json; charset=UTF-8"})
        error = data.get("error") or {}
        code = str(error.get("code") or "ok") if isinstance(error, dict) else "ok"
        if code != "ok":
            message, hint, retry, reconnect = ERROR_CODES.get(
                code, (f"TikTok refused the request ({code}).", "", False, False))
            raise PublishError(message, hint=hint, retry=retry, reconnect=reconnect)
        result = data.get("data")
        return result if isinstance(result, dict) else {}

    def profile(self, tokens: Tokens) -> Profile:
        user = self._api("/user/info/", tokens, params={"fields": "open_id,display_name,avatar_url"},
                         method="GET").get("user") or {}
        if not user.get("open_id"):
            raise PublishError("TikTok did not say which account signed in.")
        return Profile(external_id=str(user["open_id"]), name=str(user.get("display_name") or "TikTok account"),
                       extra={"avatar_url": str(user.get("avatar_url") or "")})

    # ------------------------------------------------------------------------------------------- posting
    def publish(self, tokens: Tokens, draft: Draft, *, account: str, state: dict[str, Any], remember: RememberFn,
                on_progress: ProgressFn, should_cancel: CancelFn) -> Outcome:
        size = draft.size
        if size > 4 * 1024 * MB:
            raise PublishError("TikTok takes videos up to 4 GB.")
        if state.get("publish_id") and not state.get("uploaded"):
            # cut off part-way: TikTok's upload address only lasts an hour, so start a fresh upload
            for key in ("publish_id", "uploaded"):
                state.pop(key, None)
        if not state.get("publish_id"):
            chunk, count = plan_chunks(size, self.SINGLE_MAX, self.CHUNK)
            data = self._api("/post/publish/inbox/video/init/", tokens, body={"source_info": {
                "source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}})
            publish_id, upload_url = str(data.get("publish_id") or ""), str(data.get("upload_url") or "")
            if not publish_id or not upload_url:
                raise PublishError("TikTok did not start the upload.", retry=True)
            state["publish_id"] = publish_id
            remember(state)
            self._upload(upload_url, draft, size, chunk, count, on_progress, should_cancel)
            state["uploaded"] = True
            remember(state)
        outcome = self.check(tokens, state["publish_id"], account=account, state=state)
        for wait in self.POLL_WAITS:
            if outcome.status != "processing":
                break
            pause(wait, should_cancel)
            outcome = self.check(tokens, state["publish_id"], account=account, state=state)
        return outcome

    def _upload(self, url: str, draft: Draft, size: int, chunk: int, count: int, on_progress: ProgressFn,
                should_cancel: CancelFn) -> None:
        sent = [0]

        def progress(n: int) -> None:
            sent[0] += n
            on_progress(min(1.0, sent[0] / size))

        for index in range(count):
            first = index * chunk
            last = size - 1 if index == count - 1 else first + chunk - 1
            for attempt in range(len(self.RETRY_WAITS) + 1):
                before = sent[0]
                try:
                    self.http.request("PUT", url, expect=(201, 206), headers={
                        "Content-Type": "video/mp4", "Content-Length": str(last - first + 1),
                        "Content-Range": f"bytes {first}-{last}/{size}"},
                        content=file_part(draft.file, first, last + 1, progress, should_cancel))
                    break
                except PublishError as exc:
                    sent[0] = before
                    if not exc.retry or attempt >= len(self.RETRY_WAITS):
                        raise
                    pause(self.RETRY_WAITS[attempt], should_cancel)

    def check(self, tokens: Tokens, external_id: str, *, account: str, state: dict[str, Any],
              remember: RememberFn | None = None) -> Outcome:
        data = self._api("/post/publish/status/fetch/", tokens, body={"publish_id": external_id})
        status = str(data.get("status") or "")
        if status == "SEND_TO_USER_INBOX":
            return Outcome("in_drafts", external_id, "", "In your TikTok inbox: open TikTok, tap the notification "
                                                         "and finish the post there.", check_again_in=600.0)
        if status == "PUBLISH_COMPLETE":
            ids = data.get("publicaly_available_post_id") or data.get("publicly_available_post_id") or []
            return Outcome("published", external_id, "",
                           f"Posted on TikTok (post {ids[0]})." if ids else "Posted on TikTok.")
        if status == "FAILED":
            reason = str(data.get("fail_reason") or "")
            raise PublishError(FAIL_REASONS.get(reason, f"TikTok could not take the video ({reason or 'no reason given'})."),
                               retry=reason == "internal")
        return Outcome("processing", external_id, "", "TikTok is still receiving the video.", check_again_in=30.0)
