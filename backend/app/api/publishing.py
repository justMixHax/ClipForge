"""Publishing: your developer apps, connected accounts, and the post queue."""
from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import ClipForgeError
from ..models import Account, Post
from ..publishing import assistant, oauth, service, store

router = APIRouter(prefix="/publishing", tags=["publishing"])
CONNECT = {"youtube": "sign_in", "tiktok": "sign_in", "instagram": "token"}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AppIn(_Strict):
    client_id: str = Field(max_length=300)
    client_secret: str = Field(max_length=300)


class TokenIn(_Strict):
    token: str = Field(max_length=1000)


class PostItemIn(_Strict):
    clip_id: int
    account_id: int
    title: str = Field("", max_length=300)
    caption: str = Field("", max_length=5000)
    hashtags: list[str] = Field(default_factory=list, max_length=40)
    privacy: str = Field("", max_length=20)
    options: dict[str, Any] = Field(default_factory=dict)
    scheduled_at: datetime | None = None


class QueueIn(_Strict):
    items: list[PostItemIn] = Field(min_length=1, max_length=100)
    post_again: bool = False


class SuggestIn(_Strict):
    clip_id: int
    platform: str = Field(max_length=20)


def account_out(account: Account) -> dict[str, Any]:
    return {"id": account.id, "platform": account.platform, "label": store.LABELS.get(account.platform, account.platform),
            "name": account.name, "handle": account.handle, "status": account.status, "detail": account.detail,
            "reconnect_by": account.reconnect_by, "connected_at": account.connected_at,
            "extra": {k: v for k, v in (account.extra or {}).items() if k in ("page", "avatar_url")}}


def post_out(post: Post) -> dict[str, Any]:
    return {"id": post.id, "clip_id": post.clip_id, "account_id": post.account_id, "platform": post.platform,
            "label": store.LABELS.get(post.platform, post.platform), "status": post.status,
            "scheduled_at": post.scheduled_at, "next_run_at": post.next_run_at, "title": post.title,
            "caption": post.caption, "hashtags": list(post.hashtags or []), "privacy": post.privacy,
            "options": {k: v for k, v in (post.options or {}).items() if not k.startswith("_")},
            "external_id": post.external_id, "url": post.url, "detail": post.detail, "error": post.error,
            "attempts": post.attempts, "clip_title": post.clip_title, "account_name": post.account_name,
            "created_at": post.created_at, "published_at": post.published_at,
            "thumb_url": f"/api/clips/{post.clip_id}/thumbnail" if post.clip_id else ""}


# ------------------------------------------------------------------------------------------ apps and accounts
@router.get("")
def overview(session: Session = Depends(get_db)) -> dict[str, Any]:
    """What is set up and connected. Secrets and tokens never appear here."""
    accounts = session.scalars(select(Account).order_by(Account.platform, Account.name))
    return {"platforms": [{"platform": p, "label": store.LABELS[p], "connect": CONNECT[p],
                           "redirect_uri": service.redirect_uri(p) if CONNECT[p] == "sign_in" else "",
                           **store.app_summary(session, p)} for p in store.ADAPTERS],
            "accounts": [account_out(a) for a in accounts], "daily_limit": service.daily_limit(session),
            "min_gap_hours": service.min_gap_hours(session)}


@router.put("/apps/{platform}")
def save_app(platform: str, body: AppIn, session: Session = Depends(get_db)) -> dict[str, bool]:
    store.save_app(session, platform, body.client_id, body.client_secret)
    return {"saved": True}


@router.delete("/apps/{platform}")
def remove_app(platform: str, session: Session = Depends(get_db)) -> dict[str, bool]:
    store.remove_app(session, platform)
    return {"removed": True}


@router.post("/connect/{platform}")
def connect(platform: str, session: Session = Depends(get_db)) -> dict[str, str]:
    """The platform's own sign-in page to open. It sends you back to this PC with a one-time code."""
    return {"url": service.start_connect(session, platform)}


def _page(title: str, message: str, *, ok: bool, status: int = 200) -> HTMLResponse:
    colour = "#22c55e" if ok else "#f87171"
    body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)} · ClipForge</title>
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;font-family:system-ui,sans-serif;background:#0b0d12;color:#e7e9ee;display:grid;place-items:center;min-height:100vh">
<main style="max-width:460px;padding:32px;border:1px solid #262a35;border-radius:16px;background:#12151c">
<h1 style="margin:0 0 12px;font-size:20px;color:{colour}">{html.escape(title)}</h1>
<p style="margin:0 0 20px;line-height:1.5">{html.escape(message)}</p>
<a href="/publishing?tab=accounts" style="color:#a78bfa">Back to ClipForge</a></main></body></html>"""
    return HTMLResponse(body, status_code=status)


@router.get("/oauth/callback", response_class=HTMLResponse)
def oauth_callback(state: str | None = Query(None, max_length=200), code: str | None = Query(None, max_length=2000),
                   error: str | None = Query(None, max_length=300),
                   error_description: str | None = Query(None, max_length=500),
                   session: Session = Depends(get_db)) -> HTMLResponse:
    """Where YouTube and TikTok send you back after you sign in. Only a sign-in started here is accepted."""
    if error or not code:
        oauth.finish(state)  # that sign-in is over either way
        reason = error_description or error or "no sign-in code came back"
        return _page("Not connected", f"The platform said: {reason}. ClipForge was not connected.", ok=False,
                     status=400)
    try:
        account = service.finish_connect(session, state, code)
    except ClipForgeError as exc:
        return _page("Not connected", exc.message + (f" {exc.hint}" if exc.hint else ""), ok=False, status=400)
    who = account.name + (f" ({account.handle})" if account.handle else "")
    return _page("Connected", f"{store.LABELS[account.platform]}: {who} is connected to ClipForge. You can close this "
                              "tab.", ok=True)


@router.post("/instagram/connect")
def instagram_connect(body: TokenIn, session: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [account_out(a) for a in service.connect_instagram(session, body.token)]


@router.post("/accounts/{account_id}/disconnect")
def disconnect(account_id: int, session: Session = Depends(get_db)) -> dict[str, bool]:
    service.disconnect(session, account_id)
    return {"disconnected": True}


# ---------------------------------------------------------------------------------------------------- posts
@router.get("/posts")
def list_posts(status: str | None = Query(None, max_length=30), start: datetime | None = None,
               end: datetime | None = None, limit: int = Query(500, ge=1, le=1000),
               session: Session = Depends(get_db)) -> list[dict[str, Any]]:
    query = select(Post).order_by(Post.id.desc())
    if status:
        query = query.where(Post.status == status)
    live = func.coalesce(Post.scheduled_at, Post.published_at, Post.created_at)  # as the calendar places it
    if start is not None:
        query = query.where(live >= _utc(start))
    if end is not None:
        query = query.where(live <= _utc(end))
    return [post_out(p) for p in session.scalars(query.limit(limit))]


def _utc(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


@router.post("/posts")
def queue_posts(body: QueueIn, session: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """Queue posts (one per clip and account). All or nothing: any broken rule is listed and nothing is queued."""
    requests = [service.PostRequest(clip_id=i.clip_id, account_id=i.account_id, title=i.title, caption=i.caption,
                                    hashtags=i.hashtags, privacy=i.privacy, options=i.options,
                                    scheduled_at=_utc(i.scheduled_at)) for i in body.items]
    posts = service.queue_posts(session, requests, post_again=body.post_again)
    out = [post_out(p) for p in posts]
    service.enqueue_due(session)
    return out


@router.post("/posts/{post_id}/cancel")
def cancel(post_id: int, session: Session = Depends(get_db)) -> dict[str, Any]:
    return post_out(service.cancel_post(session, post_id))


@router.post("/posts/{post_id}/retry")
def retry(post_id: int, session: Session = Depends(get_db)) -> dict[str, Any]:
    out = post_out(service.retry_post(session, post_id))
    service.enqueue_due(session)
    return out


@router.delete("/posts/{post_id}")
def remove(post_id: int, session: Session = Depends(get_db)) -> dict[str, bool]:
    service.delete_post(session, post_id)
    return {"deleted": True}


@router.post("/suggest")
def suggest(body: SuggestIn, session: Session = Depends(get_db)) -> dict[str, Any]:
    """Claude's suggestion for the title, caption and hashtags of one clip on one platform."""
    return assistant.suggest(session, body.clip_id, body.platform)
