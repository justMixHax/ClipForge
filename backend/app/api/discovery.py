"""Creator Discovery: find and follow YouTube channels, see what their new videos look like from YouTube's public
data, and import the ones you may clip. The API key never appears in an answer."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..discovery import service
from ..errors import ClipForgeError
from ..models import Creator
from ..schemas import AnalyzeIn, JobOut, MaxHeight, Permission
from .common import job_out

router = APIRouter(prefix="/discovery", tags=["discovery"])


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FindIn(_Strict):
    text: str = Field(min_length=1, max_length=500)   # a channel link, @handle, channel ID, video link or a name
    search: bool = False                              # look for a name (costs 100 quota units)


class FollowIn(_Strict):
    channel_id: str = Field(min_length=24, max_length=24)
    permission_status: Permission = "unknown"         # your right to clip this creator's videos
    permission_note: str = Field("", max_length=2000)


class RightsIn(_Strict):
    permission_status: Permission | None = None
    permission_note: str | None = Field(None, max_length=2000)


class FoundVideoPatch(_Strict):
    status: Literal["new", "dismissed"]


class ImportIn(_Strict):
    permission_status: Permission | None = None       # None: the right you recorded for the creator
    permission_note: str | None = Field(None, max_length=2000)
    remember: bool = False                            # keep this as the creator's record
    max_height: MaxHeight = 1080
    analyze: AnalyzeIn | None = None                  # set to go straight on to finding clips


@router.get("")
def overview(session: Session = Depends(get_db)) -> dict[str, Any]:
    """Whether a key is stored (never the key), the quota units used today, and the channels you follow."""
    return service.overview(session)


@router.post("/try-key")
def try_key(session: Session = Depends(get_db)) -> dict[str, Any]:
    """Ask YouTube one small question with the stored key, to see whether it works (1 unit)."""
    return service.try_key(session)


@router.post("/find")
def find(body: FindIn, session: Session = Depends(get_db)) -> dict[str, Any]:
    return service.find(session, body.text, search=body.search)


@router.post("/creators")
def follow(body: FollowIn, session: Session = Depends(get_db)) -> dict[str, Any]:
    creator, first_look = service.follow(session, body.channel_id, permission_status=body.permission_status,
                                         permission_note=body.permission_note)
    return {"creator": service.creator_out(session, creator), "first_look": first_look}


@router.patch("/creators/{creator_id}")
def set_rights(creator_id: int, body: RightsIn, session: Session = Depends(get_db)) -> dict[str, Any]:
    """Record your right to clip this creator's videos, and where it comes from."""
    creator = service.set_rights(session, creator_id, permission_status=body.permission_status,
                                 permission_note=body.permission_note)
    return service.creator_out(session, creator)


@router.delete("/creators/{creator_id}")
def unfollow(creator_id: int, session: Session = Depends(get_db)) -> dict[str, bool]:
    service.unfollow(session, creator_id)
    return {"unfollowed": True}


@router.post("/creators/{creator_id}/check")
def check_one(creator_id: int, session: Session = Depends(get_db)) -> dict[str, Any]:
    """Look at one channel now: its newest uploads, their public numbers and what their top comments say."""
    summary = service.check_one(session, creator_id)
    return {"creator": service.creator_out(session, session.get(Creator, creator_id)), "summary": summary}


@router.post("/check", response_model=JobOut)
def check_all(session: Session = Depends(get_db)) -> JobOut:
    """Queue a look at every followed channel. Asking again while it waits or runs returns the same job."""
    if not service.has_key(session):
        raise ClipForgeError("Add your YouTube Data API key first.",
                             hint="Creator Discovery shows how to get one. It is free.")
    if not session.scalar(select(func.count(Creator.id))):
        raise ClipForgeError("You are not following any channel yet.")
    return job_out(service.enqueue_check(session, everything=True))


@router.get("/videos")
def list_videos(creator_id: int | None = None, show: str = Query("new", max_length=10),
                min_minutes: float = Query(2.0, ge=0, le=600), sort: str = Query("newest", max_length=10),
                limit: int = Query(100, ge=1, le=500), session: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return service.list_videos(session, creator_id=creator_id, show=show, min_seconds=min_minutes * 60.0, sort=sort,
                               limit=limit)


@router.patch("/videos/{video_id}")
def set_video_status(video_id: int, body: FoundVideoPatch, session: Session = Depends(get_db)) -> dict[str, Any]:
    """Hide a found video from the list, or put it back."""
    return service.video_out(session, service.set_video_status(session, video_id, body.status))


@router.post("/videos/{video_id}/comments")
def read_comments(video_id: int, session: Session = Depends(get_db)) -> dict[str, Any]:
    """Read this video's top comments now (1 unit)."""
    return service.video_out(session, service.refresh_comments(session, video_id))


@router.post("/videos/{video_id}/import", response_model=JobOut)
def import_video(video_id: int, body: ImportIn, session: Session = Depends(get_db)) -> JobOut:
    """Queue the import of a found video. Nothing is downloaded until the right to clip it is stated: your
    record for the creator, or what is said here for this one video."""
    job = service.import_video(
        session, video_id, permission_status=body.permission_status, permission_note=body.permission_note,
        remember=body.remember, max_height=body.max_height,
        analyze=body.analyze.model_dump(exclude_none=True) if body.analyze is not None else None)
    return job_out(job)
