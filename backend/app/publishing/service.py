"""The publishing queue: what may be posted (the safeguards), when it runs, the job that posts it, and connecting
and disconnecting accounts.

Safeguards, checked when a post is queued and again just before it is sent:
- only clips you approved, rendered, from a source whose right to post you recorded (owned, licensed, permission);
- the same clip goes to the same account once, unless you tick "post again";
- no account gets more than the daily limit (Settings) within 24 hours;
- a post counts as published only once the platform confirms it; a passing problem is tried again twice, then
  reported; a sign-in the platform no longer accepts marks the account to reconnect.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import db
from ..config import get_settings
from ..db import utcnow
from ..errors import ClipForgeError, JobCancelled, NotFound
from ..jobs import pipeline, queue
from ..models import Account, Clip, Job, Post, Video
from ..services import settings_store, storage
from . import oauth, store
from .base import Draft, Outcome, PublishError

log = logging.getLogger("clipforge.publishing")

RIGHTS_OK = ("owned", "licensed", "permission")
LIVE = ("scheduled", "publishing", "processing", "published", "scheduled_on_platform", "private", "in_drafts")
WAITING = ("scheduled", "processing", "in_drafts")
MAX_ATTEMPTS = 3
RETRY_AFTER = (120.0, 600.0)                        # seconds before the second and the third attempt
DRAFT_CHECKS = (600.0, 3600.0, 6 * 3600.0, 24 * 3600.0, 72 * 3600.0)  # TikTok inbox: when to look again
PROCESSING_CHECKS = 30                              # about half an hour of one check a minute
LENGTHS = {"youtube": (1.0, 12 * 3600.0), "tiktok": (3.0, 600.0), "instagram": (3.0, 900.0)}
CALLBACK_PATH = "/api/publishing/oauth/callback"
OPTION_KEYS = {"made_for_kids": bool, "synthetic_media": bool, "notify_subscribers": bool, "share_to_feed": bool,
               "category_id": str}


@dataclass
class PostRequest:
    clip_id: int
    account_id: int
    title: str = ""
    caption: str = ""
    hashtags: list[str] = field(default_factory=list)
    privacy: str = ""
    options: dict[str, Any] = field(default_factory=dict)
    scheduled_at: datetime | None = None


def daily_limit(session: Session) -> int:
    return max(1, int(settings_store.get(session, "publish.daily_limit") or 6))


def _clock(moment: datetime) -> str:
    return moment.astimezone().strftime("%H:%M")


def clean_tags(tags: list[str]) -> list[str]:
    out = []
    for tag in tags or []:
        word = "".join(str(tag).split()).lstrip("#")[:60]
        if word:
            out.append(f"#{word}")
    return list(dict.fromkeys(out))[:30]


def clean_options(options: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, kind in OPTION_KEYS.items():
        if options and key in options and options[key] is not None:
            value = options[key]
            if kind is bool:
                out[key] = bool(value)
            elif str(value).isdigit():
                out[key] = str(value)[:4]
    return out


def ready_problem(clip: Clip, video: Video | None) -> str | None:
    """Why this clip may not be posted, or None. Checked at queueing and again right before sending."""
    name = f"“{clip.title}”"
    if clip.review_status != "approved":
        return f"{name} is not approved yet: approve it in the AI Clipper or the Clip Library first."
    if clip.render_status != "ready" or not clip.file_path or not storage.data_path(clip.file_path).is_file():
        return f"{name} has not been rendered yet, or its file is missing: render it first."
    rights = video.permission_status if video is not None else "unknown"
    if rights not in RIGHTS_OK:
        state = "marked as not permitted" if rights == "denied" else "not recorded"
        source = f"“{video.title}”" if video is not None else "its source"
        return (f"{name} comes from {source}, and your right to post clips of it is {state}. "
                "Record it under Source and rights first.")
    return None


def _length(clip: Clip) -> float:
    return clip.out_duration or max(0.0, clip.end - clip.start)


def text_problem(platform: str, request: PostRequest, clip: Clip) -> str | None:
    low, high = LENGTHS.get(platform, (0.0, 1e9))
    if not low <= _length(clip) <= high:
        return f"{store.LABELS[platform]} takes videos from {low:g} seconds to {high / 60:g} minutes."
    if platform == "youtube":
        title = request.title.strip()
        if not title:
            return "YouTube needs a title."
        if len(title) > 100:
            return "YouTube titles can be at most 100 characters."
        if "<" in title or ">" in title:
            return "YouTube titles cannot contain < or >."
        if request.privacy not in ("public", "unlisted", "private"):
            return "the visibility must be public, unlisted or private."
    if platform == "instagram":
        if len(request.caption) > 2200:
            return "Instagram captions can be at most 2,200 characters."
        if len(clean_tags(request.hashtags)) > 30:
            return "Instagram allows at most 30 hashtags."
    return None


def _go_live(post: Post) -> datetime:
    return post.scheduled_at or post.published_at or post.created_at


# ----------------------------------------------------------------------------------------------- queueing
def queue_posts(session: Session, requests: list[PostRequest], *, post_again: bool = False,
                now: datetime | None = None) -> list[Post]:
    """Queue clip-to-account posts. All or nothing: if any one breaks a rule, none is queued and every
    problem is listed."""
    now = now or utcnow()
    if not requests:
        raise ClipForgeError("Choose at least one clip and one account.")
    if len(requests) > 100:
        raise ClipForgeError("At most 100 posts can be queued at once.")
    limit = daily_limit(session)
    problems: list[str] = []
    planned: dict[int, list[datetime]] = {}
    rows: list[Post] = []
    for request in requests:
        clip = session.get(Clip, request.clip_id)
        account = session.get(Account, request.account_id)
        if clip is None:
            problems.append(f"Clip #{request.clip_id} no longer exists.")
            continue
        if account is None:
            problems.append("One of the chosen accounts is no longer connected.")
            continue
        name, label = f"“{clip.title}”", store.LABELS.get(account.platform, account.platform)
        problem = ready_problem(clip, session.get(Video, clip.video_id))
        if problem:
            problems.append(problem)
            continue
        if account.status != "connected":
            problems.append(f"Reconnect {account.name} ({label}) first: {account.detail or 'its sign-in has ended'}")
            continue
        when = request.scheduled_at.astimezone(timezone.utc) if request.scheduled_at is not None else None
        if when is not None and when < now - timedelta(minutes=1):
            problems.append(f"{name}: the time must be in the future.")
            continue
        if when is not None and when > now + timedelta(days=180):
            problems.append(f"{name}: posts can be scheduled up to 180 days ahead.")
            continue
        problem = text_problem(account.platform, request, clip)
        if problem:
            problems.append(f"{name} on {label}: {problem}")
            continue
        if not post_again and session.scalar(select(Post.id).where(
                Post.clip_id == clip.id, Post.account_id == account.id, Post.status.in_(LIVE)).limit(1)) is not None:
            problems.append(f"{name} is already posted or queued for {account.name} on {label}. Tick “Post again "
                            "anyway” to post it a second time.")
            continue
        moment = when or now
        times = [_go_live(p) for p in session.scalars(select(Post).where(Post.account_id == account.id,
                                                                          Post.status.in_(LIVE)))]
        if sum(abs((t - moment).total_seconds()) < 86400 for t in times + planned.get(account.id, [])) >= limit:
            problems.append(f"{account.name} on {label} would get more than {limit} posts in 24 hours. Spread them "
                            "out, or raise the limit in Settings.")
            continue
        planned.setdefault(account.id, []).append(moment)
        rows.append(Post(clip_id=clip.id, account_id=account.id, platform=account.platform, status="scheduled",
                         scheduled_at=when,
                         # YouTube schedules by itself, so a scheduled Short is uploaded straight away
                         next_run_at=now if account.platform == "youtube" or when is None else when,
                         title=request.title.strip()[:300], caption=request.caption.strip()[:5000],
                         hashtags=clean_tags(request.hashtags), privacy=request.privacy,
                         options=clean_options(request.options), clip_title=clip.title[:300],
                         account_name=account.name[:200],
                         detail="Waiting for its time." if when is not None else "Waiting to be uploaded."))
    if problems:
        raise ClipForgeError("Nothing was queued. " + " ".join(dict.fromkeys(problems)))
    session.add_all(rows)
    session.commit()
    return rows


def enqueue_due(session: Session, now: datetime | None = None) -> int:
    """Start a publishing job for every post whose time has come (also the follow-up checks). Asking again
    while a post's job waits or runs does not start a second one."""
    now = now or utcnow()
    due = list(session.scalars(select(Post).where(Post.status.in_(WAITING), Post.next_run_at.is_not(None),
                                                  Post.next_run_at <= now).order_by(Post.next_run_at)))
    for post in due:
        queue.enqueue(session, "publish", {"post_id": post.id}, priority=9, stages=pipeline.stage_list("publish"),
                      target=("post", post.id))
    return len(due)


def _get(session: Session, post_id: int) -> Post:
    post = session.get(Post, post_id)
    if post is None:
        raise NotFound("That post does not exist.")
    return post


def _jobs_of(session: Session, post_id: int) -> list[Job]:
    return [job for job in session.scalars(select(Job).where(Job.type == "publish", Job.status.in_(queue.ACTIVE)))
            if (job.payload or {}).get("post_id") == post_id]


def cancel_post(session: Session, post_id: int) -> Post:
    post = _get(session, post_id)
    if post.status == "scheduled_on_platform":
        raise ClipForgeError("This Short is already on YouTube, waiting to go live.",
                             hint="Change or delete it in YouTube Studio: ClipForge may upload, not edit.")
    if post.status not in ("scheduled", "publishing"):
        raise ClipForgeError(f"Only a post that has not gone out yet can be cancelled (this one is "
                             f"{post.status.replace('_', ' ')}).")
    for job in _jobs_of(session, post.id):
        queue.request_cancel(session, job.id)
    post.status, post.next_run_at, post.detail = "cancelled", None, "Cancelled."
    session.commit()
    return post


def retry_post(session: Session, post_id: int, now: datetime | None = None) -> Post:
    post = _get(session, post_id)
    if post.status not in ("failed", "cancelled"):
        raise ClipForgeError("Only a failed or cancelled post can be tried again.")
    clip = session.get(Clip, post.clip_id) if post.clip_id else None
    account = session.get(Account, post.account_id) if post.account_id else None
    if clip is None or account is None:
        raise ClipForgeError("The clip or the account of this post is gone, so it cannot be tried again.")
    problem = ready_problem(clip, session.get(Video, clip.video_id))
    if problem:
        raise ClipForgeError(problem)
    if account.status != "connected":
        raise ClipForgeError(f"Reconnect {account.name} first.")
    state = (post.options or {}).get("_state") or {}
    already = state.get("video_id") or state.get("media") or (state.get("publish_id") if state.get("uploaded") else "")
    if already:  # it reached the platform before: look at it there instead of uploading a second copy
        post.status, post.external_id = "processing", str(already)
    else:
        post.options = {k: v for k, v in (post.options or {}).items() if not k.startswith("_")}
        post.status = "scheduled"
    post.next_run_at, post.error, post.attempts, post.detail = now or utcnow(), "", 0, "Queued again."
    session.commit()
    return post


def delete_post(session: Session, post_id: int) -> None:
    """Remove a post from the list. It does not delete anything on the platform."""
    post = _get(session, post_id)
    if post.status == "publishing" or any(job.status == "running" for job in _jobs_of(session, post.id)):
        raise ClipForgeError("It is uploading right now: cancel it first.")
    for job in _jobs_of(session, post.id):
        queue.request_cancel(session, job.id)
    session.delete(post)
    session.commit()


# ----------------------------------------------------------------------------------------------- the job
def _fail(post_id: int, message: str) -> ClipForgeError:
    with db.session_scope() as s:
        post = s.get(Post, post_id)
        if post is not None:
            post.status, post.error, post.next_run_at, post.detail = "failed", message, None, ""
    return ClipForgeError(message)


def run_publish(ctx: Any, job: dict[str, Any]) -> dict[str, Any]:
    """Post one clip, or check on one the platform is still working on. Called by the worker (pipeline.run_job)."""
    post_id = int(job["payload"]["post_id"])
    ctx.start("prepare")
    with db.session_scope() as s:
        post = s.get(Post, post_id)
        if post is None:
            raise NotFound("This post no longer exists.")
        if post.status not in WAITING:
            for key in ("upload", "confirm"):
                ctx.skip(key, "Nothing to do")
            return {"post_id": post_id, "status": post.status, "note": "Nothing to do: this post is "
                                                                     f"{post.status.replace('_', ' ')}."}
        checking = post.status in ("processing", "in_drafts")
        label = store.LABELS.get(post.platform, post.platform)
        title = post.clip_title or post.title
        account = s.get(Account, post.account_id) if post.account_id else None
        if account is None:
            raise _fail(post_id, "The account it was going to has been disconnected.")
        clip = s.get(Clip, post.clip_id) if post.clip_id else None
        draft = None
        if not checking:
            if clip is None:
                raise _fail(post_id, "The clip no longer exists.")
            problem = ready_problem(clip, s.get(Video, clip.video_id))
            if problem:
                raise _fail(post_id, problem)
        try:
            adapter = store.adapter(s, post.platform)
        except ClipForgeError as exc:  # your app's ID and secret were removed: stop, do not try every 10 seconds
            raise _fail(post_id, exc.message + (f" {exc.hint}" if exc.hint else "")) from exc
        try:
            tokens = store.fresh_tokens(s, account, adapter)
        except PublishError as exc:
            _after_error(post_id, exc, checking=checking, label=label, title=title, ctx=ctx)
            raise
        if not checking:
            publish_at = post.scheduled_at if (post.platform == "youtube" and post.scheduled_at is not None
                                               and post.scheduled_at > utcnow() + timedelta(minutes=2)) else None
            draft = Draft(file=storage.data_path(clip.file_path), title=post.title, caption=post.caption,
                          hashtags=list(post.hashtags or []), privacy=post.privacy, publish_at=publish_at,
                          options=dict(post.options or {}))
            post.status, post.attempts, post.error = "publishing", post.attempts + 1, ""
            post.detail = f"Uploading to {label}…"
        state = dict((post.options or {}).get("_state") or {})
        external, where = post.external_id, account.external_id

    def remember(new_state: dict[str, Any]) -> None:
        with db.session_scope() as s:
            row = s.get(Post, post_id)
            if row is not None:
                row.options = {**(row.options or {}), "_state": dict(new_state)}

    try:
        if checking:
            ctx.skip("upload", "Uploaded before")
            ctx.start("confirm", f"Asking {label} how it is going")
            outcome = adapter.check(tokens, external, account=where, state=state, remember=remember)
        else:
            ctx.start("upload", f"Uploading to {label}")
            outcome = adapter.publish(tokens, draft, account=where, state=state, remember=remember,
                                      on_progress=ctx.progress, should_cancel=ctx.cancelled)
            ctx.start("confirm")
    except PublishError as exc:
        _after_error(post_id, exc, checking=checking, label=label, title=title, ctx=ctx)
        raise
    except JobCancelled:
        raise
    except Exception as exc:  # a bug or an answer nobody expected: report it once, never retry it by itself
        log.warning("publishing went wrong unexpectedly", exc_info=True)
        _after_error(post_id, PublishError(f"Something unexpected went wrong ({type(exc).__name__}: {exc})."[:300]),
                     checking=checking, label=label, title=title, ctx=ctx)
        raise
    finally:
        adapter.http.close()
    _after_outcome(post_id, outcome, label=label, title=title, ctx=ctx)
    return {"post_id": post_id, "status": outcome.status, "url": outcome.url}


def _after_error(post_id: int, exc: PublishError, *, checking: bool, label: str, title: str, ctx: Any) -> None:
    now = utcnow()
    again = None
    with db.session_scope() as s:
        post = s.get(Post, post_id)
        if post is None:
            return
        if exc.reconnect and post.account_id and s.get(Account, post.account_id) is not None:
            account = s.get(Account, post.account_id)
            account.status, account.detail = "needs_reconnect", exc.message[:500]
        full = exc.message + (f" {exc.hint}" if exc.hint else "")
        if checking and exc.retry:
            post.next_run_at = now + timedelta(minutes=5)
            post.detail = f"{label} did not answer the check; ClipForge asks again in a few minutes."
        elif not checking and exc.retry and not exc.reconnect and post.attempts < MAX_ATTEMPTS:
            again = now + timedelta(seconds=RETRY_AFTER[min(post.attempts - 1, len(RETRY_AFTER) - 1)])
            post.status, post.next_run_at, post.error = "scheduled", again, exc.message
            post.detail = f"{exc.message} ClipForge tries again at {_clock(again)}."
        else:
            post.status, post.error, post.next_run_at, post.detail = "failed", full, None, ""
            pipeline.notify(s, "job_failed", f"A post to {label} failed: “{title}”", full, "/publishing")
    ctx.say("forge", f"Could not post “{title}” to {label}: {exc.message}"
                     + (f" Trying again at {_clock(again)}." if again else ""))


def _after_outcome(post_id: int, outcome: Outcome, *, label: str, title: str, ctx: Any) -> None:
    now = utcnow()
    with db.session_scope() as s:
        post = s.get(Post, post_id)
        previous = post.status
        checks = int((post.options or {}).get("_checks", 0))
        post.status, post.detail, post.error = outcome.status, outcome.detail, ""
        post.external_id = outcome.external_id or post.external_id
        post.url = outcome.url or post.url
        post.next_run_at = None
        if outcome.status == "in_drafts":
            if checks < len(DRAFT_CHECKS):
                post.next_run_at = now + timedelta(seconds=DRAFT_CHECKS[checks])
            else:
                post.detail = "Still waiting in your TikTok inbox. Post it from the TikTok app; ClipForge has stopped checking."
            post.options = {**(post.options or {}), "_checks": checks + 1}
        elif outcome.status == "processing":
            if checks < PROCESSING_CHECKS:
                post.next_run_at = now + timedelta(seconds=outcome.check_again_in or 60.0)
            else:
                post.detail = f"{label} is still processing it. ClipForge has stopped checking: look on {label}."
            post.options = {**(post.options or {}), "_checks": checks + 1}
        if outcome.status == "published" and post.published_at is None:
            post.published_at = now
        when = post.scheduled_at
        lines = {
            "published": f"Posted “{title}” to {post.account_name} on {label}.",
            "scheduled_on_platform": f"Uploaded “{title}” to {label}; it goes live "
                                     + (f"at {_clock(when)} on {when.astimezone():%d %b}." if when else "as scheduled."),
            "private": f"Uploaded “{title}” to {label} as private.",
            "in_drafts": f"Sent “{title}” to your TikTok inbox: finish the post in the TikTok app.",
            "processing": f"Uploaded “{title}” to {label}; it is still processing.",
        }
        line = lines.get(outcome.status, f"{label} says: {outcome.detail}")
        if outcome.status != previous:
            pipeline.notify(s, "info", f"{label}: “{title}”", outcome.detail, "/publishing")
    ctx.say("forge", line)


# ------------------------------------------------------------------------------------------------ accounts
def redirect_uri(platform: str) -> str:
    """Where a platform sends you back after signing in: this PC only. Google documents only the bare loopback
    address for desktop apps (the home page passes the code on to the callback); TikTok takes the callback
    address itself, registered in your TikTok app."""
    base = f"http://127.0.0.1:{get_settings().port}"
    return base if platform == "youtube" else base + CALLBACK_PATH


def start_connect(session: Session, platform: str) -> str:
    store.check_platform(platform)
    if platform == "instagram":
        raise ClipForgeError("Instagram connects with a token you paste, not with a sign-in page.")
    platform_adapter = store.adapter(session, platform)
    try:
        address = redirect_uri(platform)
        state, verifier = oauth.begin(platform, address)
        return platform_adapter.authorize_url(address, state, verifier)
    finally:
        platform_adapter.http.close()


def finish_connect(session: Session, state: str | None, code: str) -> Account:
    pending = oauth.finish(state)
    if pending is None:
        raise ClipForgeError("This sign-in has expired or was already used.", hint="Press Connect again in ClipForge.")
    platform_adapter = store.adapter(session, pending.platform)
    try:
        tokens = platform_adapter.exchange(code, pending.redirect_uri, pending.verifier)
        profile = platform_adapter.profile(tokens)
    finally:
        platform_adapter.http.close()
    return store.upsert_account(session, pending.platform, profile, tokens)


def connect_instagram(session: Session, token: str) -> list[Account]:
    if not token.strip() or len(token) > 1000:
        raise ClipForgeError("Paste the token from Meta's Graph API Explorer.")
    platform_adapter = store.adapter(session, "instagram")
    try:
        tokens, profiles = platform_adapter.connect_with_token(token)
    finally:
        platform_adapter.http.close()
    return [store.upsert_account(session, "instagram", profile, tokens) for profile in profiles]


def disconnect(session: Session, account_id: int) -> None:
    """Revoke ClipForge's access where the platform allows it, delete the stored sign-in and the account.
    Posts already made stay on the platform and in the list; posts still waiting are cancelled."""
    account = session.get(Account, account_id)
    if account is None:
        raise NotFound("That account is not connected.")
    tokens = store.load_tokens(session, account.id)
    if tokens is not None:
        try:
            platform_adapter = store.adapter(session, account.platform)
            platform_adapter.revoke(tokens)
            platform_adapter.http.close()
        except ClipForgeError:
            pass
    store.forget_tokens(session, account.id)
    for post in session.scalars(select(Post).where(Post.account_id == account.id, Post.status.in_(WAITING))):
        if post.status == "scheduled":
            post.status, post.next_run_at, post.detail = "cancelled", None, "Cancelled: the account was disconnected."
        else:
            post.next_run_at = None
    session.delete(account)
    session.commit()
