"""Creator Discovery: the channels you follow, what was found on them, and importing what you may clip.

Finding a video here gives no right to clip it. A found video is a suggestion; importing it is the same step as
importing any link, and it starts only once the right to clip it is stated (your record for the creator, or
what you say for that one video).

The database is never left waiting on YouTube: every request to it is made first, and what came back is written
in one short step afterwards, so a slow answer cannot hold up the job queue's own writes.
"""
from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import utcnow
from ..errors import ClipForgeError, Conflict, DependencyMissing, JobCancelled, NotFound
from ..jobs import queue
from ..models import PERMISSION_STATUSES, Clip, Creator, CreatorVideo, Job, Setting, Video
from ..security.secrets import SecretError
from ..services import settings_store
from ..services.timecode import youtube_id
from . import signals
from .youtube_api import DAILY_UNITS, DiscoveryError, YouTubeData

log = logging.getLogger("clipforge.discovery")

KEY_NAME = "youtube.api_key"
USAGE_KEY = "discovery.usage"        # {"day", "units"}: ClipForge's own count of the quota units it used today
PAUSE_KEY = "discovery.paused"       # {"until", "why"}: no checks by itself until then (quota used up, key refused)
UPLOADS_PER_CHECK = 25
COMMENT_CHECKS_PER_RUN = 12
COMMENTS_AGAIN_AFTER = timedelta(hours=20)
COMMENTS_FOR = timedelta(days=14)    # a video's comments are read again while it is this young
LONG_ENOUGH = 120.0                  # seconds: anything shorter is a Short or a trailer, not something to clip from
KEEP_FOR = timedelta(days=30)        # YouTube's API rules: what was read is refreshed or deleted within 30 days
RETRY_AFTER = timedelta(hours=1)     # after a check that did not work
PAUSE_FOR = timedelta(hours=6)
MAX_CREATORS = 100
CAN_CLIP = ("owned", "licensed", "permission")
RIGHTS_HINT = ("Choose “my own content”, “licence or campaign” or “the creator said so”. A video being public "
               "does not make it free to repost.")
KEY_CHECK_CHANNEL = "UC_x5XG1OV2P6uZZ5FSM9Ttw"   # Google's own developer channel: asked for to try a key out


# ------------------------------------------------------------------------------------------ key and quota
def has_key(session: Session) -> bool:
    return bool(settings_store.secrets_present(session).get(KEY_NAME))


def usage(session: Session, now: datetime | None = None) -> dict[str, Any]:
    """The quota units ClipForge itself used today (YouTube's day, which starts at midnight Pacific time)."""
    today = signals.pacific_day(now or utcnow())
    row = session.get(Setting, USAGE_KEY)
    value = row.value if row is not None and isinstance(row.value, dict) else {}
    return {"day": today, "units": int(value.get("units") or 0) if value.get("day") == today else 0,
            "limit": DAILY_UNITS}


def _spend(session: Session, units: int) -> None:
    if units <= 0:
        return
    today = signals.pacific_day(utcnow())
    row = session.get(Setting, USAGE_KEY)
    value = row.value if row is not None and isinstance(row.value, dict) else {}
    total = (int(value.get("units") or 0) if value.get("day") == today else 0) + units
    if row is None:
        session.add(Setting(key=USAGE_KEY, value={"day": today, "units": total}))
    else:
        row.value = {"day": today, "units": total}
    session.commit()


@contextlib.contextmanager
def youtube(session: Session) -> Iterator[YouTubeData]:
    """The YouTube Data API with your stored key. Whatever happens inside, the units it used are counted."""
    try:
        key = settings_store.read_secret(session, KEY_NAME)
    except SecretError as exc:   # the data folder came from another PC or Windows account
        raise DiscoveryError("The stored YouTube API key cannot be read on this PC.",
                             hint="It was saved on another PC or under another Windows account. Paste the key again "
                                  "under Creator Discovery → API key.", key=True) from exc
    if not key:
        raise DiscoveryError("Add your YouTube Data API key first.",
                             hint="Creator Discovery shows how to get one. It is free.", key=True)
    api = YouTubeData(key)
    try:
        yield api
    except BaseException:
        session.rollback()
        raise
    finally:
        api.close()
        try:
            _spend(session, api.spent)
        except Exception:  # noqa: BLE001  (a count that could not be saved must not hide what really happened)
            session.rollback()
            log.warning("could not record the YouTube quota used", exc_info=True)


def try_key(session: Session) -> dict[str, Any]:
    """Ask YouTube for one public channel, to find out whether the stored key works. Costs 1 unit."""
    with youtube(session) as api:
        found = api.channels(ids=[KEY_CHECK_CHANNEL])
    _clear_pause(session)
    return {"ok": True, "saw": found[0]["title"] if found else ""}


def _paused(session: Session, now: datetime) -> dict[str, Any] | None:
    row = session.get(Setting, PAUSE_KEY)
    value = row.value if row is not None and isinstance(row.value, dict) else None
    until = signals.parse_when(value.get("until")) if value else None
    return {"until": until, "why": str(value.get("why") or "")} if until is not None and until > now else None


def _pause(session: Session, why: str, now: datetime) -> None:
    value = {"until": (now + PAUSE_FOR).isoformat(), "why": why[:500]}
    row = session.get(Setting, PAUSE_KEY)
    if row is None:
        session.add(Setting(key=PAUSE_KEY, value=value))
    else:
        row.value = value
    session.commit()


def _clear_pause(session: Session) -> None:
    row = session.get(Setting, PAUSE_KEY)
    if row is not None:
        session.delete(row)
        session.commit()


# ------------------------------------------------------------------------------------------- finding
SHOWN = ("id", "title", "handle", "url", "description", "thumb_url", "subscribers", "video_count")


def find(session: Session, text: str, *, search: bool = False) -> dict[str, Any]:
    """Channels for what was typed: a channel link, an @handle, a channel ID or a link to one of its videos
    (1 or 2 units), or, only when search is asked for, a name (100 units)."""
    kind, value = signals.channel_query(text)
    if kind == "empty":
        raise ClipForgeError("Paste a channel's link or @handle, or type its name.")
    if kind == "other":
        raise ClipForgeError("That link is not a YouTube channel or video.",
                             hint="Creator Discovery follows YouTube channels. Paste a link like "
                                  "https://www.youtube.com/@name, or a link to one of the channel's videos.")
    if kind == "words" and not search:
        raise ClipForgeError("That is not a channel link, an @handle or a channel ID.",
                             hint="To look for it by name, press Search by name. That costs 100 of your 10,000 "
                                  "daily units; a link or an @handle costs 1.")
    with youtube(session) as api:
        if kind == "id":
            channels = api.channels(ids=[value])
        elif kind == "handle":
            channels = api.channels(handle=value)
        elif kind == "username":
            channels = api.channels(username=value)
        elif kind == "custom":   # most old custom addresses became the channel's @handle
            channels = api.channels(handle="@" + value.lstrip("@"))
            if not channels and search:
                channels = api.search_channels(value)
        elif kind == "video":
            videos = api.videos([value])
            channels = api.channels(ids=[videos[0]["channel_id"]]) if videos and videos[0]["channel_id"] else []
        else:
            channels = api.search_channels(value)
        spent = api.spent
    if not channels:
        if kind == "words":
            raise NotFound(f"No channel was found for “{value[:60]}”.", hint="Try the channel's link or @handle.")
        if kind == "video":
            raise NotFound("YouTube has no public video with that link.")
        if kind == "custom":
            raise NotFound("That old-style channel address could not be matched to a channel.",
                           hint="Open the channel on YouTube and copy its @handle link, or press Search by name "
                                "(100 units).")
        raise NotFound("YouTube has no channel with that " + ("ID." if kind == "id" else "name."),
                       hint="Check the spelling, or paste a link to one of the channel's videos.")
    followed = {c.external_id: c.id for c in session.scalars(
        select(Creator).where(Creator.platform == "youtube", Creator.external_id.in_([c["id"] for c in channels])))}
    return {"channels": [{**{name: channel[name] for name in SHOWN}, "creator_id": followed.get(channel["id"])}
                         for channel in channels], "units": spent}


# ------------------------------------------------------------------------------------------ following
def _creator(session: Session, creator_id: int) -> Creator:
    creator = session.get(Creator, creator_id)
    if creator is None:
        raise NotFound("You are not following that channel (it may have been removed).")
    return creator


def _apply_channel(creator: Creator, channel: dict[str, Any]) -> None:
    creator.title = channel["title"] or creator.title or "Channel"
    creator.handle, creator.url = channel["handle"], channel["url"]
    creator.thumb_url, creator.description = channel["thumb_url"], channel["description"]
    creator.subscribers, creator.video_count = channel["subscribers"], channel["video_count"]
    creator.uploads_playlist = channel["uploads"]


def _check_rights(status: str) -> None:
    if status not in PERMISSION_STATUSES:
        raise ClipForgeError("Unknown rights status.")


def follow(session: Session, channel_id: str, *, permission_status: str = "unknown",
           permission_note: str = "") -> tuple[Creator, dict[str, Any]]:
    """Start following a channel and take a first look at its newest uploads. Returns the creator and what the
    first look found (or why it could not be done: the channel is followed either way)."""
    _check_rights(permission_status)
    if signals.channel_query(channel_id)[0] != "id":
        raise ClipForgeError("That is not a YouTube channel ID.")
    existing = session.scalar(select(Creator).where(Creator.platform == "youtube", Creator.external_id == channel_id))
    if existing is not None:
        raise Conflict(f"You already follow {existing.title}.")
    if (session.scalar(select(func.count(Creator.id))) or 0) >= MAX_CREATORS:
        raise ClipForgeError(f"You can follow up to {MAX_CREATORS} channels. Unfollow one you no longer need first.")
    with youtube(session) as api:
        found = api.channels(ids=[channel_id])
        if not found:
            raise NotFound("YouTube has no channel with that ID.")
        creator = Creator(platform="youtube", external_id=channel_id, permission_status=permission_status,
                          permission_note=permission_note.strip()[:2000])
        _apply_channel(creator, found[0])
        session.add(creator)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise Conflict("You already follow that channel.") from None
        try:
            summary = _check(session, creator, api, channel=found[0])
        except DiscoveryError as exc:   # followed, but its uploads could not be looked at yet
            summary = {"error": exc.message + (f" {exc.hint}" if exc.hint else "")}
    if "error" not in summary:
        _clear_pause(session)
    log.info("channel followed", extra={"creator_id": creator.id})
    return creator, summary


def unfollow(session: Session, creator_id: int) -> None:
    """Stop following a channel. What was found on it goes too; videos you imported stay in your library."""
    session.delete(_creator(session, creator_id))
    session.commit()


def set_rights(session: Session, creator_id: int, *, permission_status: str | None = None,
               permission_note: str | None = None) -> Creator:
    creator = _creator(session, creator_id)
    if permission_status is not None:
        _check_rights(permission_status)
        creator.permission_status = permission_status
    if permission_note is not None:
        creator.permission_note = permission_note.strip()[:2000]
    session.commit()
    return creator


# -------------------------------------------------------------------------------------------- checking
def _apply_video(row: CreatorVideo, video: dict[str, Any], now: datetime) -> None:
    row.title, row.description = video["title"], video["description"]
    row.published_at, row.duration = video["published_at"], float(video["duration"] or 0.0)
    row.live, row.was_live = video["live"], bool(video["was_live"])
    row.views, row.likes, row.comments = video["views"], video["likes"], video["comments"]
    row.thumb_url, row.stats_at = video["thumb_url"], now


def _wants_comments(row: CreatorVideo, now: datetime) -> bool:
    """Whether a video's top comments should be read now: a finished video long enough to clip from, never read
    before, or still young and last read most of a day ago."""
    if row.live != "none" or row.duration < LONG_ENOUGH or row.comments == 0:
        return False
    if not row.signals or not isinstance(row.signals, dict):   # never read, or kept in a shape nobody expects
        return True
    read_at = signals.parse_when(row.signals.get("fetched_at"))
    young = row.published_at is None or row.published_at >= now - COMMENTS_FOR
    return young and (read_at is None or read_at <= now - COMMENTS_AGAIN_AFTER)


def _signals_for(comments: list[dict[str, Any]] | None, duration: float, now: datetime) -> dict[str, Any]:
    if comments is None:
        return {"off": True, "checked": 0, "laughing": 0, "moments": [], "fetched_at": now.isoformat()}
    return signals.comment_signals(comments, duration, now)


def _check(session: Session, creator: Creator, api: YouTubeData, *, channel: dict[str, Any] | None = None,
           now: datetime | None = None, should_cancel: Callable[[], bool] | None = None,
           comment_checks: int = COMMENT_CHECKS_PER_RUN) -> dict[str, Any]:
    """Look at a channel again: its public numbers, its newest uploads with theirs, and what the top comments of
    the long ones say. Returns what was found. A problem is kept on the creator (check_error) and raised."""
    now = now or utcnow()
    creator_id, first = creator.id, creator.checked_at is None
    try:
        # ---- ask YouTube (nothing is written while it answers) ----------------------------------------------
        if channel is None:
            found = api.channels(ids=[creator.external_id])
            if not found:
                raise DiscoveryError("YouTube no longer lists this channel.",
                                     hint="It may have been closed. Unfollow it, then look for it again.")
            channel = found[0]
        ids = api.uploads(channel["uploads"], UPLOADS_PER_CHECK) if channel["uploads"] else []
        details = api.videos(ids) if ids else []

        # ---- write what came back ------------------------------------------------------------------------------
        _apply_channel(creator, channel)
        known = {row.external_id: row for row in session.scalars(
            select(CreatorVideo).where(CreatorVideo.creator_id == creator_id))}
        seen: list[CreatorVideo] = []
        added = 0
        for video in details:
            if video["channel_id"] and video["channel_id"] != creator.external_id:
                continue   # not theirs: never listed under their name
            row = known.get(video["id"])
            is_new = row is None
            if row is None:
                row = CreatorVideo(creator_id=creator_id, external_id=video["id"], found_at=now)
                session.add(row)
                known[video["id"]] = row
            _apply_video(row, video, now)
            seen.append(row)
            added += int(is_new and row.live == "none" and row.duration >= LONG_ENOUGH)
        for row in list(known.values()):   # what YouTube has not confirmed for 30 days is not kept
            if row.stats_at < now - KEEP_FOR:
                session.delete(row)
        creator.checked_at = creator.tried_at = now
        creator.check_error = ""
        session.commit()
    except IntegrityError:
        session.rollback()
        raise Conflict("This channel was being checked at the same moment. Try again.") from None
    except DiscoveryError as exc:
        _record_problem(session, creator_id, exc, now)
        raise

    # ---- what the top comments say: ask first, write afterwards -----------------------------------------------
    waiting = sorted((row for row in seen if _wants_comments(row, now)),
                     key=lambda row: (row.signals is not None, -(row.published_at.timestamp() if row.published_at else 0)))
    read: list[tuple[CreatorVideo, list[dict[str, Any]] | None]] = []
    problem: DiscoveryError | None = None
    for row in waiting[:max(0, comment_checks)]:
        if should_cancel is not None and should_cancel():
            break
        try:
            read.append((row, api.top_comments(row.external_id)))
        except DiscoveryError as exc:
            problem = exc
            break
    for row, comments in read:
        row.signals = _signals_for(comments, row.duration, now)
    if problem is not None:
        creator.check_error = problem.message + (f" {problem.hint}" if problem.hint else "")
    session.commit()
    if problem is not None:
        raise problem
    return {"creator_id": creator_id, "found": len(seen), "new": added, "first": first, "comments_read": len(read)}


def _record_problem(session: Session, creator_id: int, problem: DiscoveryError, now: datetime) -> None:
    session.rollback()
    creator = session.get(Creator, creator_id)
    if creator is not None:
        creator.tried_at = now
        creator.check_error = problem.message + (f" {problem.hint}" if problem.hint else "")
        session.commit()


def check_one(session: Session, creator_id: int) -> dict[str, Any]:
    """Look at one channel now."""
    creator = _creator(session, creator_id)
    with youtube(session) as api:
        summary = _check(session, creator, api)
    _clear_pause(session)
    return summary


def refresh_comments(session: Session, video_id: int) -> CreatorVideo:
    """Read one video's top comments now (1 unit)."""
    row = _video(session, video_id)
    if row.live != "none":
        raise ClipForgeError("This stream has not ended yet, so its comments say little about its moments.")
    with youtube(session) as api:
        comments = api.top_comments(row.external_id)
    row.signals = _signals_for(comments, row.duration, utcnow())
    session.commit()
    return row


def _due(creator: Creator, hours: float, now: datetime) -> bool:
    if creator.tried_at is not None and creator.tried_at > now - RETRY_AFTER:
        return False
    return creator.checked_at is None or creator.checked_at <= now - timedelta(hours=hours)


def enqueue_check(session: Session, *, everything: bool = True) -> Job:
    """Queue a check of the followed channels: all of them, or only those that are due. A check that is already
    waiting or running is returned instead of a second one, with one exception. Asking for all of them must
    not be answered with a check of the due ones: one still waiting becomes a check of all, and behind one that
    has started (it has made its list) a check of all is queued."""
    from ..jobs import pipeline  # the job module builds on this one

    pending = list(session.scalars(select(Job).where(Job.type == "discover", Job.status.in_(queue.ACTIVE))
                                   .order_by(Job.id)))
    if not everything and pending:
        return pending[0]
    for job in pending:
        if (job.payload or {}).get("all"):
            return job
    for job in pending:
        # only while it is still waiting: the same condition the worker's claim uses, so one of the two wins
        widened = session.execute(update(Job).where(Job.id == job.id, Job.status == "queued")
                                  .values(payload={**(job.payload or {}), "all": True}))
        session.commit()
        if widened.rowcount == 1:
            session.refresh(job)
            return job
    return queue.enqueue(session, "discover", {"all": bool(everything)}, priority=2,
                         stages=pipeline.stage_list("discover"),
                         target=("discover", "all" if everything else "due"))


def enqueue_due(session: Session, now: datetime | None = None) -> Job | None:
    """For the worker: queue a check when you asked for checks by themselves and a channel is due for one."""
    hours = float(settings_store.get(session, "discovery.check_hours") or 0)
    if hours <= 0 or not has_key(session):
        return None
    now = now or utcnow()
    if _paused(session, now) is not None:
        return None
    if not any(_due(creator, hours, now) for creator in session.scalars(select(Creator))):
        return None
    return enqueue_check(session, everything=False)


def run_check_job(ctx: Any, job: dict[str, Any]) -> dict[str, Any]:
    """The "discover" job: look at every followed channel (or only those that are due), one after the other."""
    from .. import db
    from ..jobs import pipeline

    everything = bool((job.get("payload") or {}).get("all"))
    ctx.start("check")
    session = db.new_session()
    try:
        return _run_check(ctx, session, everything, pipeline)
    except JobCancelled:
        raise
    except Exception as exc:
        # Whatever stopped the whole check, the worker must not start it again a minute later and fail the same
        # way: checks by themselves wait until a look works again (Look now, Try the key) or the pause is over.
        why = exc.message if isinstance(exc, DiscoveryError) else (
            f"The last check stopped with an unexpected problem ({type(exc).__name__}). "
            "The details are in data\\logs\\worker.log.")
        try:
            session.rollback()
            _pause(session, why, utcnow())
        except Exception:  # noqa: BLE001  (the reason the check stopped is what has to be reported)
            log.warning("could not pause the checks of followed channels", exc_info=True)
        raise
    finally:
        session.close()


def _run_check(ctx: Any, session: Session, everything: bool, pipeline: Any) -> dict[str, Any]:
    now = utcnow()
    hours = float(settings_store.get(session, "discovery.check_hours") or 0)
    creators = [c for c in session.scalars(select(Creator).order_by(Creator.checked_at.is_not(None),
                                                                    Creator.checked_at, Creator.id))
                if everything or _due(c, max(hours, 1.0), now)]
    if not creators:
        ctx.say("forge", "No followed channel needed checking.")
        return {"channels": 0, "new": 0, "note": "No followed channel needed checking."}
    checked, new_total, failed, stopped = 0, 0, [], None
    with youtube(session) as api:
        for index, creator in enumerate(creators):
            ctx.check()
            creator_id, title = creator.id, creator.title
            ctx.progress(index / len(creators), f"Checking {title} on YouTube")
            try:
                summary = _check(session, creator, api, should_cancel=ctx.cancelled)
            except DiscoveryError as exc:
                if exc.key or exc.quota:   # the next channel would be refused the same way
                    stopped = exc
                    for later in creators[index + 1:]:
                        later.tried_at = now
                    session.commit()
                    break
                failed.append(f"{title}: {exc.message}")
                continue
            except Conflict:
                continue   # being checked from the app at this very moment
            except Exception as exc:  # noqa: BLE001
                # Something nobody expected about this one channel, such as an answer that is not what YouTube's
                # documentation describes. It stays with this channel: the others are still looked at, and this
                # one waits an hour like any channel whose look did not work.
                log.warning("a channel could not be checked", exc_info=True, extra={"creator_id": creator_id})
                problem = DiscoveryError("What YouTube answered about this channel could not be read.",
                                         hint="ClipForge tries again in an hour. If it stays, the details are in "
                                              "data\\logs\\worker.log.")
                _record_problem(session, creator_id, problem, now)
                failed.append(f"{title}: {problem.message} ({type(exc).__name__})")
                continue
            checked += 1
            new_total += summary["new"]
            if summary["new"] and not summary["first"]:
                count = summary["new"]
                pipeline.notify(session, "info", f"{count} new video{'s' if count != 1 else ''} from {title}",
                                "Creator Discovery shows what viewers say about " + ("them." if count != 1 else "it."),
                                "/discovery")
                session.commit()
        spent = api.spent
    used = usage(session)
    line = (f"Checked {checked} channel{'s' if checked != 1 else ''} on YouTube: {new_total} new "
            f"video{'s' if new_total != 1 else ''}. That used {spent} of YouTube's daily units "
            f"({used['units']:,} of {used['limit']:,} today).")
    ctx.say("forge", line)
    for problem_line in failed[:5]:
        ctx.say("forge", f"Could not check {problem_line}")
    if stopped is not None:
        raise stopped   # the caller pauses the checks by themselves, with YouTube's reason
    _clear_pause(session)
    return {"channels": checked, "new": new_total, "units": spent, "failed": len(failed), "note": line}


# --------------------------------------------------------------------------------------------- listing
def creator_out(session: Session, creator: Creator) -> dict[str, Any]:
    waiting = session.scalar(select(func.count(CreatorVideo.id)).where(
        CreatorVideo.creator_id == creator.id, CreatorVideo.status == "new", CreatorVideo.live == "none",
        CreatorVideo.duration >= LONG_ENOUGH)) or 0
    return {"id": creator.id, "platform": creator.platform, "external_id": creator.external_id,
            "title": creator.title, "handle": creator.handle, "url": creator.url, "thumb_url": creator.thumb_url,
            "description": creator.description, "subscribers": creator.subscribers,
            "video_count": creator.video_count, "permission_status": creator.permission_status,
            "permission_note": creator.permission_note, "check_error": creator.check_error,
            "followed_at": creator.followed_at, "checked_at": creator.checked_at, "videos_listed": waiting}


def _video(session: Session, video_id: int) -> CreatorVideo:
    row = session.get(CreatorVideo, video_id)
    if row is None:
        raise NotFound("That video is no longer in the list. Check the channel again.")
    return row


def _library(session: Session) -> dict[str, Video]:
    """Videos in your library that came from a YouTube link, by YouTube id (the newest import of each)."""
    found: dict[str, Video] = {}
    for video in session.scalars(select(Video).where(Video.source_type == "url").order_by(Video.id)):
        link = youtube_id(video.source_url)
        if link:
            found[link] = video
    return found


def _clip_numbers(session: Session, video_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Per library video: how many clips it has, the best score, and the same for the funny ones. Rejected clips
    are left out."""
    numbers: dict[int, dict[str, Any]] = {}
    if not video_ids:
        return numbers
    rows = session.execute(select(Clip.video_id, Clip.category, Clip.score).where(
        Clip.video_id.in_(video_ids), Clip.review_status != "rejected"))
    for video_id, category, score in rows:
        entry = numbers.setdefault(video_id, {"clips": 0, "best": None, "funny": 0, "best_funny": None})
        entry["clips"] += 1
        entry["best"] = max(entry["best"] or 0.0, float(score or 0.0))
        if category == "funny":
            entry["funny"] += 1
            entry["best_funny"] = max(entry["best_funny"] or 0.0, float(score or 0.0))
    return numbers


def video_url(external_id: str, at: float | None = None) -> str:
    return f"https://www.youtube.com/watch?v={external_id}" + (f"&t={int(at)}s" if at else "")


def list_videos(session: Session, *, creator_id: int | None = None, show: str = "new", min_seconds: float = LONG_ENOUGH,
                sort: str = "newest", limit: int = 100, now: datetime | None = None) -> list[dict[str, Any]]:
    """The uploads found on the channels you follow, with what YouTube's public data says about each and, for
    those already in your library, how their clips scored."""
    if show not in ("new", "library", "hidden", "all"):
        raise ClipForgeError("Unknown list: use new, library, hidden or all.")
    if sort not in ("newest", "laughs", "standout", "moments"):
        raise ClipForgeError("Unknown order: use newest, laughs, standout or moments.")
    now = now or utcnow()
    query = select(CreatorVideo)
    if creator_id is not None:
        query = query.where(CreatorVideo.creator_id == creator_id)
    rows = list(session.scalars(query))
    creators = {c.id: c for c in session.scalars(select(Creator))}
    library = _library(session)
    numbers = _clip_numbers(session, [v.id for v in library.values()])
    importing: dict[str, Job] = {}
    for job in session.scalars(select(Job).where(Job.type == "import", Job.status.in_(queue.ACTIVE))):
        link = youtube_id(str((job.payload or {}).get("url") or ""))
        if link:
            importing[link] = job
    usual: dict[int, list[tuple[int, int]]] = {}   # per channel: (row id, views) of its finished long videos
    for row in session.scalars(select(CreatorVideo).where(CreatorVideo.live == "none",
                                                          CreatorVideo.duration >= LONG_ENOUGH,
                                                          CreatorVideo.views.is_not(None))):
        usual.setdefault(row.creator_id, []).append((row.id, row.views))

    out = []
    for row in rows:
        mine = library.get(row.external_id)
        if show == "new" and (row.status != "new" or mine is not None or row.live == "upcoming"):
            continue
        if show == "library" and mine is None:
            continue
        if show == "hidden" and row.status != "dismissed":
            continue
        if show != "library" and row.live == "none" and row.duration < min_seconds:
            continue
        creator = creators.get(row.creator_id)
        long_enough = row.live == "none" and row.duration >= LONG_ENOUGH
        ratio = signals.standout(row.views, [views for other, views in usual.get(row.creator_id, [])
                                             if other != row.id]) if long_enough else None
        said = row.signals if isinstance(row.signals, dict) and row.signals else None
        job = importing.get(row.external_id)
        out.append({
            "id": row.id, "creator_id": row.creator_id, "creator_title": creator.title if creator else "",
            "creator_rights": creator.permission_status if creator else "unknown",
            "external_id": row.external_id, "url": video_url(row.external_id), "title": row.title,
            "description": row.description, "published_at": row.published_at, "duration": row.duration,
            "live": row.live, "was_live": row.was_live, "views": row.views, "likes": row.likes,
            "comments": row.comments, "thumb_url": row.thumb_url, "status": row.status, "stats_at": row.stats_at,
            "fresh": bool(row.published_at and row.published_at > now - timedelta(hours=48)),
            "standout": ratio,
            "signals": None if said is None else {
                "off": bool(said.get("off")), "checked": int(said.get("checked") or 0),
                "laughing": int(said.get("laughing") or 0), "fetched_at": said.get("fetched_at"),
                "moments": [{**moment, "url": video_url(row.external_id, moment.get("t"))}
                            for moment in said.get("moments") or [] if isinstance(moment, dict)]},
            "library": None if mine is None else {
                "video_id": mine.id, "status": mine.status, "title": mine.title,
                **numbers.get(mine.id, {"clips": 0, "best": None, "funny": 0, "best_funny": None})},
            "importing": None if job is None else {"job_id": job.id, "status": job.status, "progress": job.progress,
                                                   "message": job.message},
        })

    def newest(item: dict[str, Any]) -> float:
        return -(item["published_at"].timestamp() if item["published_at"] else 0.0)

    def laugh_share(item: dict[str, Any]) -> float:
        said = item["signals"]
        return said["laughing"] / said["checked"] if said and said["checked"] >= 20 else -1.0

    def pointed(item: dict[str, Any]) -> int:
        said = item["signals"]
        return sum(int(m.get("likes") or 0) + int(m.get("mentions") or 0) for m in said["moments"]) if said else -1

    if sort == "laughs":
        out.sort(key=lambda item: (-laugh_share(item), newest(item)))
    elif sort == "standout":
        out.sort(key=lambda item: (-(item["standout"] if item["standout"] is not None else -1.0), newest(item)))
    elif sort == "moments":
        out.sort(key=lambda item: (-pointed(item), newest(item)))
    else:
        out.sort(key=newest)
    return out[:max(1, min(int(limit), 500))]


def video_out(session: Session, row: CreatorVideo) -> dict[str, Any]:
    """One video, as the list shows it."""
    found = [item for item in list_videos(session, creator_id=row.creator_id, show="all", min_seconds=0, limit=500)
             if item["id"] == row.id]
    if not found:
        raise NotFound("That video is no longer in the list. Check the channel again.")
    return found[0]


def set_video_status(session: Session, video_id: int, status: str) -> CreatorVideo:
    if status not in ("new", "dismissed"):
        raise ClipForgeError("A found video is either in the list (new) or hidden (dismissed).")
    row = _video(session, video_id)
    row.status = status
    session.commit()
    return row


# -------------------------------------------------------------------------------------------- importing
def import_video(session: Session, video_id: int, *, permission_status: str | None = None,
                 permission_note: str | None = None, remember: bool = False, max_height: int = 1080,
                 analyze: dict[str, Any] | None = None) -> Job:
    """Queue the import of a found video, the same way as a pasted link: nothing is downloaded until the right
    to clip it is stated. That is your record for the creator unless you say otherwise for this video; remember
    keeps what you say as the creator's record."""
    from ..jobs import pipeline
    from ..services import downloader

    row = _video(session, video_id)
    creator = _creator(session, row.creator_id)
    if creator.permission_status == "denied":
        # Your record for the creator comes first. A right named for one video does not get around it: the
        # record is changed on the channel's card, as a step of its own.
        raise ClipForgeError(f"You marked {creator.title or 'this creator'} as not allowed for clipping.",
                             hint="If that has changed, change your record on the channel's card first.")
    if row.live != "none":
        raise ClipForgeError("This stream is live right now." if row.live == "live" else
                             "This video has not started yet.",
                             hint="Import it after it has ended, when the whole recording is available.")
    if permission_status is not None:
        _check_rights(permission_status)
    status = permission_status or creator.permission_status
    note = creator.permission_note if permission_note is None else permission_note.strip()[:2000]
    if status not in CAN_CLIP:   # not recorded, or "not allowed" said for this one video
        raise ClipForgeError("Say why you are allowed to clip this video before importing it.", hint=RIGHTS_HINT)
    mine = _library(session).get(row.external_id)
    if mine is not None:
        raise Conflict(f"This video is already in your library as “{mine.title}”.")
    url = downloader.validate_source_url(video_url(row.external_id))
    if not downloader.available():
        raise DependencyMissing("Importing from a link needs the yt-dlp package, which is not installed.",
                                hint="Run scripts\\setup.ps1.")
    if remember and permission_status in CAN_CLIP:
        creator.permission_status, creator.permission_note = status, note
        session.commit()
    job = pipeline.enqueue_import(session, url, permission_status=status, permission_note=note,
                                  creator_name=creator.title, creator_handle=creator.handle,
                                  max_height=max_height, analyze=analyze)
    log.info("import queued from discovery", extra={"creator_id": creator.id, "job_id": job.id})
    return job


def viewer_moments(session: Session, video: Video) -> list[dict[str, Any]]:
    """For the clip picker: the moments viewers pointed at in the comments of this video, when it was found
    through Creator Discovery and its comments were read. Empty otherwise."""
    link = youtube_id(video.source_url) if video.source_type == "url" else None
    if not link:
        return []
    row = session.scalars(select(CreatorVideo).where(CreatorVideo.external_id == link)
                          .order_by(CreatorVideo.id.desc())).first()
    said = row.signals if row is not None and isinstance(row.signals, dict) else {}
    moments = said.get("moments") if isinstance(said.get("moments"), list) else []
    usable = [m for m in moments if isinstance(m, dict) and isinstance(m.get("t"), (int, float))
              and not isinstance(m.get("t"), bool) and 0 <= m["t"] < max(video.duration, 1.0)]
    return usable[:8]


# ---------------------------------------------------------------------------------------------- overview
def suggestions(session: Session, limit: int = 8) -> list[dict[str, Any]]:
    """Creators whose videos you already imported from YouTube and do not follow yet (by name), most videos
    first. Following one takes one request: the channel is looked up from the link of one of its videos."""
    followed = {c.title.casefold() for c in session.scalars(select(Creator))}
    found: dict[str, dict[str, Any]] = {}
    for video in session.scalars(select(Video).where(Video.source_type == "url").order_by(Video.id.desc())):
        name = (video.creator_name or "").strip()
        if not name or name.casefold() in followed or not youtube_id(video.source_url):
            continue
        entry = found.setdefault(name.casefold(), {"name": name, "videos": 0, "link": video.source_url})
        entry["videos"] += 1
    return sorted(found.values(), key=lambda item: (-item["videos"], item["name"].casefold()))[:limit]


def overview(session: Session) -> dict[str, Any]:
    now = utcnow()
    paused = _paused(session, now)
    active = session.scalars(select(Job).where(Job.type == "discover", Job.status.in_(queue.ACTIVE))
                             .order_by(Job.id.desc())).first()
    creators = list(session.scalars(select(Creator).order_by(func.lower(Creator.title), Creator.id)))
    return {"key_set": has_key(session), "usage": usage(session, now),
            "check_hours": float(settings_store.get(session, "discovery.check_hours") or 0),
            "paused": None if paused is None else {"until": paused["until"], "why": paused["why"]},
            "checking": None if active is None else {"job_id": active.id, "status": active.status,
                                                     "progress": active.progress, "message": active.message},
            "creators": [creator_out(session, c) for c in creators],
            "suggestions": suggestions(session) if has_key(session) else []}
