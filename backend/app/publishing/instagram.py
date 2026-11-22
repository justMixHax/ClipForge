"""Instagram Reels through the Instagram Graph API with Facebook Login for Business.

That route is the only documented way to send a video file straight from this PC (a resumable upload to
rupload.facebook.com), so no public address for the clip is needed and nothing on this PC is exposed. It needs a
professional Instagram account (Creator or Business) linked to a Facebook Page.

Sign-in: you make a token for your own Meta app in Meta's Graph API Explorer and paste it here once. The backend
exchanges it for a 60-day token with your app secret. Meta does not let an app renew that kind of token by
itself, so after about 60 days you paste a new one; ClipForge says when.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from .base import (CancelFn, Draft, Http, Outcome, Profile, ProgressFn, PublishError, RememberFn, Tokens, file_part,
                   pause)

PERMISSIONS = ("instagram_basic", "instagram_content_publish", "pages_show_list", "pages_read_engagement",
               "business_management")
LONG_LIVED = 60 * 86400.0
MB = 1024 * 1024
RECONNECT_HINT = "Make a new token in Meta's Graph API Explorer and paste it under Publishing → Accounts."


class MetaHttp(Http):
    def error_for(self, response: httpx.Response) -> PublishError:
        try:
            error = response.json().get("error") or {}
        except (ValueError, AttributeError):
            error = {}
        code = error.get("code") if isinstance(error, dict) else None
        message = str(error.get("error_user_msg") or error.get("message") or "") if isinstance(error, dict) else ""
        if code == 190:
            return PublishError(f"Meta no longer accepts this sign-in ({message or 'token expired'}).",
                                hint=RECONNECT_HINT, reconnect=True)
        if code in (4, 17, 32, 613):
            return PublishError("Meta says there have been too many requests.", hint="ClipForge tries again later.",
                                retry=True)
        if isinstance(error, dict) and error.get("error_subcode") == 2207042:
            return PublishError("Instagram's limit of posts through the API in 24 hours is reached.",
                                hint="Try again later.", retry=True)
        return super().error_for(response)


class Instagram:
    platform = "instagram"
    label = "Instagram"
    ENDPOINTS = {"graph": "https://graph.facebook.com/v27.0", "rupload": "https://rupload.facebook.com/ig-api-upload/v27.0"}
    RETRY_WAITS: tuple[float, ...] = (5.0, 15.0, 45.0)
    POLL_WAITS: tuple[float, ...] = (10.0, 15.0, 30.0, 60.0, 60.0, 60.0)  # Meta asks for about one check a minute

    def __init__(self, app_id: str, app_secret: str, *, endpoints: dict[str, str] | None = None,
                 http: Http | None = None) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.urls = {**self.ENDPOINTS, **(endpoints or {})}
        self.http = http or MetaHttp(self.label)

    @staticmethod
    def _auth(tokens: Tokens) -> dict[str, str]:
        return {"Authorization": f"OAuth {tokens.access_token}"}  # in a header, never in the address

    # ------------------------------------------------------------------------------------------- sign-in
    def connect_with_token(self, pasted: str) -> tuple[Tokens, list[Profile]]:
        """Exchange a token you made in the Graph API Explorer for a 60-day one, and find your Instagram account."""
        try:
            data = self.http.json("POST", f"{self.urls['graph']}/oauth/access_token", expect=(200,), data={
                "grant_type": "fb_exchange_token", "client_id": self.app_id, "client_secret": self.app_secret,
                "fb_exchange_token": pasted.strip()})
        except PublishError as exc:
            if exc.retry:
                raise
            raise PublishError(f"Meta did not accept that token. {exc.message}",
                               hint="Make a new one for your own app in the Graph API Explorer with the permissions "
                                    "listed, and paste it within the hour.") from exc
        access = str(data.get("access_token") or "")
        if not access:
            raise PublishError("Meta did not send back a long-lived token.")
        lifetime = float(data.get("expires_in") or LONG_LIVED)
        now = time.time()
        tokens = Tokens(access_token=access, expires_at=now + lifetime, refresh_expires_at=now + lifetime,
                        scopes=list(PERMISSIONS))
        return tokens, self.accounts(tokens)

    def accounts(self, tokens: Tokens) -> list[Profile]:
        data = self.http.json("GET", f"{self.urls['graph']}/me/accounts", headers=self._auth(tokens),
                              params={"fields": "name,instagram_business_account{id,username,name}", "limit": "100"})
        profiles = []
        for page in data.get("data") or []:
            ig = page.get("instagram_business_account") or {}
            if not ig.get("id"):
                continue
            username = str(ig.get("username") or "")
            profiles.append(Profile(external_id=str(ig["id"]), name=str(ig.get("name") or username or page.get("name", "")),
                                    handle=f"@{username}" if username else "",
                                    extra={"page": str(page.get("name") or ""), "page_id": str(page.get("id") or "")}))
        if not profiles:
            raise PublishError("None of the Facebook Pages this sign-in can see has an Instagram professional account "
                               "linked to it.",
                               hint="In Instagram: switch to a professional account and link it to a Facebook Page. "
                                    "Then make the token again with the pages_show_list permission.")
        return profiles

    def refresh(self, tokens: Tokens) -> Tokens:
        raise PublishError("This Instagram sign-in has run out.", hint=RECONNECT_HINT, reconnect=True)

    def revoke(self, tokens: Tokens) -> None:
        try:
            self.http.request("DELETE", f"{self.urls['graph']}/me/permissions", headers=self._auth(tokens),
                              expect=(200, 400))
        except PublishError:
            pass

    # ------------------------------------------------------------------------------------------- posting
    @staticmethod
    def caption(draft: Draft) -> str:
        tags = [t.strip() for t in draft.hashtags if t.strip()][:30]  # Instagram allows 30 hashtags
        text = (draft.caption or "").strip()
        full = f"{text}\n\n{' '.join(tags)}".strip() if tags else text
        return full[:2200]

    def publish(self, tokens: Tokens, draft: Draft, *, account: str, state: dict[str, Any], remember: RememberFn,
                on_progress: ProgressFn, should_cancel: CancelFn) -> Outcome:
        if state.get("media"):
            return self._published(tokens, state["media"])  # published before: never post it twice
        size = draft.size
        if size > 300 * MB:
            raise PublishError("Instagram takes Reels files up to 300 MB.")
        if not state.get("container"):
            data = self.http.json("POST", f"{self.urls['graph']}/{account}/media", headers=self._auth(tokens), data={
                "media_type": "REELS", "upload_type": "resumable", "caption": self.caption(draft),
                "share_to_feed": "true" if draft.options.get("share_to_feed", True) else "false"})
            container = str(data.get("id") or "")
            if not container:
                raise PublishError("Instagram did not start the upload.", retry=True)
            state.update(container=container, upload_uri=str(data.get("uri") or f"{self.urls['rupload']}/{container}"),
                         uploaded=False)
            remember(state)
        if not state.get("uploaded"):
            self._upload(state["upload_uri"], tokens, draft, size, on_progress, should_cancel)
            state["uploaded"] = True
            remember(state)
        outcome = self.check(tokens, state["container"], account=account, state=state, remember=remember)
        for wait in self.POLL_WAITS:
            if outcome.status != "processing":
                break
            pause(wait, should_cancel)
            outcome = self.check(tokens, state["container"], account=account, state=state, remember=remember)
        return outcome

    def _upload(self, uri: str, tokens: Tokens, draft: Draft, size: int, on_progress: ProgressFn,
                should_cancel: CancelFn) -> None:
        for attempt in range(len(self.RETRY_WAITS) + 1):
            sent = [0]

            def progress(n: int) -> None:
                sent[0] += n
                on_progress(min(1.0, sent[0] / size))

            try:
                self.http.request("POST", uri, expect=(200,), headers={
                    **self._auth(tokens), "offset": "0", "file_size": str(size),
                    "Content-Type": "application/octet-stream", "Content-Length": str(size)},
                    content=file_part(draft.file, 0, size, progress, should_cancel))
                return
            except PublishError as exc:
                if not exc.retry or attempt >= len(self.RETRY_WAITS):
                    raise
                pause(self.RETRY_WAITS[attempt], should_cancel)

    def check(self, tokens: Tokens, external_id: str, *, account: str, state: dict[str, Any],
              remember: RememberFn | None = None) -> Outcome:
        if state.get("media"):
            return self._published(tokens, state["media"])
        data = self.http.json("GET", f"{self.urls['graph']}/{external_id}", headers=self._auth(tokens),
                              params={"fields": "status_code,status"})
        code = str(data.get("status_code") or "")
        if code == "FINISHED":
            done = self.http.json("POST", f"{self.urls['graph']}/{account}/media_publish", headers=self._auth(tokens),
                                  data={"creation_id": external_id})
            media = str(done.get("id") or "")
            if not media:
                raise PublishError("Instagram did not confirm the post.", hint="Check Instagram before retrying, so "
                                                                               "it is not posted twice.")
            state["media"] = media
            if remember is not None:
                remember(state)
            return self._published(tokens, media)
        if code == "PUBLISHED":
            return Outcome("published", external_id, "", "Live on Instagram.")
        if code == "ERROR":
            raise PublishError(f"Instagram could not process the video ({data.get('status') or 'no reason given'}).",
                               hint="Reels must be MP4, 3 seconds to 15 minutes long and under 300 MB.")
        if code == "EXPIRED":
            state.clear()
            if remember is not None:
                remember(state)
            raise PublishError("The upload expired: Instagram keeps an unpublished upload for 24 hours.",
                               hint="Retry the post to upload it again.")
        return Outcome("processing", external_id, "", "Uploaded. Instagram is still processing it.", check_again_in=60.0)

    def _published(self, tokens: Tokens, media: str) -> Outcome:
        try:
            data = self.http.json("GET", f"{self.urls['graph']}/{media}", headers=self._auth(tokens),
                                  params={"fields": "permalink"})
            url = str(data.get("permalink") or "")
        except PublishError:
            url = ""
        return Outcome("published", media, url, "Live on Instagram.")
