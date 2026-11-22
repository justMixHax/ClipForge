"""Source videos: upload, link from disk, playback, transcript, analysis."""
from __future__ import annotations

import logging
import mimetypes
import os
import shutil
from pathlib import Path
from urllib.parse import unquote

import numpy as np
from fastapi import APIRouter, Depends, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analysis.audio import AudioFeatures
from ..analysis.visual import VisualFeatures
from ..config import ALLOWED_VIDEO_EXTENSIONS, get_settings
from ..db import get_db
from ..errors import ClipForgeError, DependencyMissing, NotFound, TooLarge, UnsupportedType
from ..jobs import pipeline, queue
from ..models import Clip, Job, TranscriptSegment, Video
from ..schemas import (AnalyzeIn, ClipCreate, ClipOut, ImportPathIn, ImportUrlIn, InspectUrlIn, JobOut, LinkInfoOut,
                       MomentIn,
                       SegmentOut, SegmentPatch, VideoOut, VideoPatch)
from ..security.paths import PathSecurityError, has_allowed_extension, safe_filename, validate_import_path
from ..services import downloader, ffmpeg, storage
from ..services.timecode import parse_time, youtube_id
from ..services.library import register_video
from .common import clip_out, ensure_project, get_video, job_out, video_out

router = APIRouter(prefix="/videos", tags=["videos"])
log = logging.getLogger("clipforge.api.videos")
SUPPORTED = ", ".join(e.lstrip(".").upper() for e in ALLOWED_VIDEO_EXTENSIONS)


@router.post("/upload", response_model=VideoOut)
async def upload_video(request: Request, title: str | None = Query(None, max_length=300),
                       project_id: int | None = None, session: Session = Depends(get_db)) -> VideoOut:
    """Upload a video as the raw request body. The original file name goes in the X-Filename header
    (URL-encoded). The file is streamed straight to disk, so large recordings do not use memory."""
    settings = get_settings()
    ensure_project(session, project_id)  # before a single byte is written
    name = safe_filename(unquote(request.headers.get("x-filename", "")), default="video")
    if not has_allowed_extension(name, ALLOWED_VIDEO_EXTENSIONS):
        raise UnsupportedType(f"Unsupported file type. Supported: {SUPPORTED}.")
    limit = settings.max_upload_bytes
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise TooLarge(f"This file is larger than the {settings.max_upload_gb:g} GB upload limit.",
                       hint="Use “Add from disk” for very large recordings: it links the file without copying it.")

    key = storage.new_key()
    folder = settings.uploads_dir / key
    folder.mkdir(parents=True)
    target = folder / f"source{Path(name).suffix.lower()}"
    part = folder / "upload.part"
    try:
        received = 0
        with open(part, "wb") as fh:
            async for chunk in request.stream():
                received += len(chunk)
                if received > limit:
                    raise TooLarge(f"This file is larger than the {settings.max_upload_gb:g} GB upload limit.")
                fh.write(chunk)
        if received == 0:
            raise ClipForgeError("The upload was empty.")
        os.replace(part, target)
        info = await run_in_threadpool(ffmpeg.probe, target)
        video, duplicate = await run_in_threadpool(
            lambda: register_video(session, target, key=key, mode="managed", original_name=name, title=title, info=info,
                              thumb_folder=folder, source_type="upload", project_id=project_id))
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    log.info("video uploaded", extra={"video_id": video.id, "bytes": received, "seconds": round(info.duration, 1)})
    return video_out(session, video, duplicate)


@router.post("/import-path", response_model=VideoOut)
def import_from_disk(body: ImportPathIn, session: Session = Depends(get_db)) -> VideoOut:
    """Add a video that is already on this PC without copying it. The original is never changed or deleted."""
    try:
        path = validate_import_path(body.path, ALLOWED_VIDEO_EXTENSIONS)
    except PathSecurityError as exc:
        raise ClipForgeError(str(exc)) from exc
    ensure_project(session, body.project_id)
    info = ffmpeg.probe(path)
    key = storage.new_key()
    video, duplicate = register_video(
        session, path, key=key, mode="linked", original_name=path.name, title=body.title, info=info,
        thumb_folder=get_settings().work_dir / key, source_type="path", creator_name=body.creator_name,
        permission_status=body.permission_status, permission_note=body.permission_note, source_url=body.source_url,
        project_id=body.project_id)
    return video_out(session, video, duplicate)


@router.post("/inspect-url", response_model=LinkInfoOut)
def inspect_url(body: InspectUrlIn, session: Session = Depends(get_db)) -> LinkInfoOut:
    """Look at a link without downloading it: what the video is, how long, and how big it would be."""
    url = downloader.validate_source_url(body.url)
    found = downloader.inspect_link(url, max_height=body.max_height)
    limit = get_settings().max_upload_bytes
    existing = session.scalar(select(Video.id).where(Video.source_url == found.url).order_by(Video.id).limit(1))
    return LinkInfoOut(**found.as_dict(), over_limit=bool(found.approx_bytes and found.approx_bytes > limit),
                       limit_gb=get_settings().max_upload_gb, existing_video_id=existing)


@router.post("/import-url", response_model=JobOut)
def import_url(body: ImportUrlIn, session: Session = Depends(get_db)) -> JobOut:
    """Queue the import of one public video from a link. Nothing is downloaded until you have said why you
    are allowed to clip it; that answer is stored with the video and written into every clip's post text.
    Asking again for the same link while it is waiting or downloading returns the same job."""
    url = downloader.validate_source_url(body.url)
    if body.permission_status in ("unknown", "denied"):
        raise ClipForgeError("Say why you are allowed to clip this video before importing it.",
                             hint="Choose “my own content”, “licence or campaign” or “the creator said so”. A video "
                                  "being public does not make it free to repost.")
    ensure_project(session, body.project_id)
    if not downloader.available():
        raise DependencyMissing("Importing from a link needs the yt-dlp package, which is not installed.",
                                hint="Run scripts\\setup.ps1.")
    job = pipeline.enqueue_import(
        session, url, permission_status=body.permission_status, permission_note=body.permission_note,
        creator_name=body.creator_name, title=body.title, project_id=body.project_id, max_height=body.max_height,
        analyze=body.analyze.model_dump(exclude_none=True) if body.analyze is not None else None)
    return job_out(job)


@router.get("", response_model=list[VideoOut])
def list_videos(q: str | None = Query(None, max_length=200), status: str | None = None, project_id: int | None = None,
                session: Session = Depends(get_db)) -> list[VideoOut]:
    query = select(Video).order_by(Video.created_at.desc(), Video.id.desc())
    if q:
        like = f"%{q.strip()}%"
        query = query.where(Video.title.ilike(like) | Video.creator_name.ilike(like))
    if status:
        query = query.where(Video.status == status)
    if project_id is not None:
        query = query.where(Video.project_id == project_id)
    return [video_out(session, v) for v in session.scalars(query)]


@router.get("/{video_id}", response_model=VideoOut)
def read_video(video_id: int, session: Session = Depends(get_db)) -> VideoOut:
    return video_out(session, get_video(session, video_id))


@router.patch("/{video_id}", response_model=VideoOut)
def update_video(video_id: int, body: VideoPatch, session: Session = Depends(get_db)) -> VideoOut:
    video = get_video(session, video_id)
    for field in body.model_fields_set:
        value = getattr(body, field)
        if value is None and field != "project_id":
            continue
        if field == "project_id":
            ensure_project(session, value)
            for clip in session.scalars(select(Clip).where(Clip.video_id == video.id)):
                clip.project_id = value  # a video's clips always live in the same project as the video
        setattr(video, field, value)
    session.commit()
    return video_out(session, video)


@router.delete("/{video_id}")
def delete_video(video_id: int, session: Session = Depends(get_db)) -> dict:
    """Delete a video, its clips and its working files. A linked original on disk is left untouched."""
    video = get_video(session, video_id)
    settings = get_settings()
    for job in session.scalars(select(Job).where(Job.video_id == video.id, Job.status.in_(queue.ACTIVE))):
        queue.request_cancel(session, job.id)
    freed = 0
    for clip in session.scalars(select(Clip).where(Clip.video_id == video.id)):
        freed += storage.remove_tree(settings.clips_dir / clip.storage_key)
    freed += storage.remove_tree(settings.work_dir / video.storage_key)
    if video.storage_mode == "managed":
        freed += storage.remove_tree(settings.uploads_dir / video.storage_key)
    session.delete(video)
    session.commit()
    return {"deleted": True, "freed_bytes": freed, "original_kept": video.storage_mode == "linked"}


@router.get("/{video_id}/stream")
def stream_video(video_id: int, session: Session = Depends(get_db)) -> FileResponse:
    """The source video for the player. Range requests are honoured, so seeking works."""
    path = storage.video_source_path(get_video(session, video_id))
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "video/mp4")


@router.get("/{video_id}/thumbnail")
def video_thumbnail(video_id: int, session: Session = Depends(get_db)) -> FileResponse:
    video = get_video(session, video_id)
    if not video.thumb_path:
        raise NotFound("No thumbnail for this video")
    return FileResponse(storage.data_path(video.thumb_path), media_type="image/jpeg")


@router.post("/{video_id}/analyze", response_model=JobOut)
def analyze_video(video_id: int, body: AnalyzeIn, session: Session = Depends(get_db)) -> JobOut:
    """Queue the analysis. Asking again while it is queued or running returns the same job."""
    video = get_video(session, video_id)
    storage.video_source_path(video)  # fail now, with a clear message, if the file has gone missing
    options = body.model_dump(exclude_none=True)
    job = pipeline.enqueue_analysis(session, video, options)
    return job_out(job)


@router.post("/{video_id}/moments", response_model=JobOut)
def clip_a_moment(video_id: int, body: MomentIn, session: Session = Depends(get_db)) -> JobOut:
    """Make a clip from a moment you chose. Your start is kept exactly; ClipForge does the same work as for the
    moments it finds (end point, title, hook, score, second opinion, render). Asking twice queues it once."""
    video = get_video(session, video_id)
    if video.permission_status == "denied":
        raise ClipForgeError("This source is marked as not permitted for clipping.",
                             hint="Change that under Source and rights if it is wrong.")
    storage.video_source_path(video)  # fail now, with a clear message, if the file has gone missing
    own = youtube_id(video.source_url)
    for value in (body.start, body.end):
        linked = youtube_id(value) if isinstance(value, str) else None
        if linked and own and linked != own:
            raise ClipForgeError("That link is for a different video than the one open here.",
                                 hint="Open that video in ClipForge first, or copy the link from this video.")
    start = parse_time(body.start)
    end = None if body.end is None or (isinstance(body.end, str) and not body.end.strip()) else parse_time(body.end)
    clock = pipeline._clock
    if start >= video.duration - 0.5:
        raise ClipForgeError(f"This video is only {clock(video.duration)} long, so a moment cannot start at "
                             f"{clock(start)}.")
    if end is not None:
        if end <= start:
            raise ClipForgeError("The end must come after the start.")
        end = min(end, video.duration)
        if end - start < 1.0:
            raise ClipForgeError("A clip must be at least 1 second long.")
        if end - start > 600:
            raise ClipForgeError("A clip can be at most 10 minutes long.")
    options = body.options.model_dump(exclude_none=True) if body.options is not None else {}
    return job_out(pipeline.enqueue_moment(session, video, start=start, end=end, options=options))


@router.get("/{video_id}/transcript", response_model=list[SegmentOut])
def read_transcript(video_id: int, start: float | None = None, end: float | None = None,
                    session: Session = Depends(get_db)) -> list[TranscriptSegment]:
    get_video(session, video_id)
    query = select(TranscriptSegment).where(TranscriptSegment.video_id == video_id).order_by(TranscriptSegment.idx)
    if start is not None:
        query = query.where(TranscriptSegment.end >= start)
    if end is not None:
        query = query.where(TranscriptSegment.start <= end)
    return list(session.scalars(query))


@router.patch("/{video_id}/transcript/{segment_id}", response_model=SegmentOut)
def edit_transcript(video_id: int, segment_id: int, body: SegmentPatch,
                    session: Session = Depends(get_db)) -> TranscriptSegment:
    """Correct a sentence. Word timings are kept when the number of words is unchanged; otherwise the
    sentence's time span is shared out across the new words in proportion to their length."""
    segment = session.get(TranscriptSegment, segment_id)
    if segment is None or segment.video_id != video_id:
        raise NotFound("That transcript line does not exist")
    tokens = body.text.split()
    if not tokens:
        raise ClipForgeError("A transcript line cannot be empty")
    old = segment.words or []
    if len(tokens) == len(old):
        words = [[t, w[1], w[2], w[3] if len(w) > 3 else 1.0] for t, w in zip(tokens, old)]
    else:
        span = max(segment.end - segment.start, 0.05 * len(tokens))
        weights = [max(1, len(t)) for t in tokens]
        total, cursor, words = sum(weights), segment.start, []
        for token, weight in zip(tokens, weights):
            length = span * weight / total
            words.append([token, round(cursor, 3), round(cursor + length, 3), 1.0])
            cursor += length
    segment.text, segment.words, segment.edited = " ".join(tokens), words, True
    session.commit()
    session.refresh(segment)
    return segment


@router.get("/{video_id}/timeline")
def timeline(video_id: int, start: float = 0.0, end: float | None = None, points: int = Query(600, ge=50, le=4000),
             session: Session = Depends(get_db)) -> dict:
    """Loudness curve, loud moments, scene cuts and existing clips for drawing a timeline."""
    video = get_video(session, video_id)
    end = video.duration if end is None else min(end, video.duration)
    start = max(0.0, min(start, end))
    work = get_settings().work_dir / video.storage_key
    envelope: list[float] = []
    loud: list[dict] = []
    cuts: list[float] = []
    if (work / "audio.npz").exists():
        audio = AudioFeatures.load(work / "audio.npz")
        a, b = int(start / audio.hop), max(int(start / audio.hop) + 1, int(end / audio.hop))
        level = audio.rms_db[a:b]
        if level.size:
            floor = min(audio.silence_db, float(np.percentile(audio.rms_db, 5)))
            scaled = np.clip((level - floor) / max(float(audio.rms_db.max()) - floor, 1e-6), 0, 1)
            edges = np.linspace(0, scaled.size, min(points, scaled.size) + 1).astype(int)
            envelope = [round(float(scaled[i:max(j, i + 1)].max()), 3) for i, j in zip(edges[:-1], edges[1:])]
        loud = [e for e in audio.loud_events if e["end"] >= start and e["start"] <= end]
    if (work / "visual.npz").exists():
        cuts = [t for t in VisualFeatures.load(work / "visual.npz").cuts if start <= t <= end]
    clips = [{"id": c.id, "start": c.start, "end": c.end, "title": c.title, "score": c.score,
              "review_status": c.review_status}
             for c in session.scalars(select(Clip).where(Clip.video_id == video.id, Clip.end >= start,
                                                         Clip.start <= end).order_by(Clip.start))]
    return {"start": start, "end": end, "duration": video.duration, "envelope": envelope, "loud_events": loud,
            "cuts": cuts, "clips": clips, "analysed": bool(envelope)}


@router.post("/{video_id}/clips", response_model=ClipOut)
def create_manual_clip(video_id: int, body: ClipCreate, session: Session = Depends(get_db)) -> ClipOut:
    """Make a clip by hand from a time range. It gets the default edit settings and can be rendered at once."""
    video = get_video(session, video_id)
    if body.end - body.start < 0.3:
        raise ClipForgeError("A clip must be at least 0.3 seconds long, and end after it starts.")
    if body.end > video.duration + 0.05:
        raise ClipForgeError(f"This video is only {video.duration:.1f} seconds long.")
    words = pipeline.load_words(session, video.id, body.start, body.end)
    text = " ".join(w.text for w in words if w.start >= body.start - 0.01 and w.end <= body.end + 0.01)
    settings = pipeline.AnalyzeOptions.from_payload({}).clip_settings()
    clip = Clip(video_id=video.id, project_id=video.project_id, storage_key=storage.new_key(), origin="manual",
                title=(body.title or f"Clip at {pipeline._clock(body.start)}")[:300], start=round(body.start, 3),
                end=round(body.end, 3), transcript_text=text, analysis_source="manual",
                explanation="Made by hand, so it has no automatic score.", settings=settings,
                hook_text="", description=body.title or "")
    session.add(clip)
    session.commit()
    session.refresh(clip)
    return clip_out(session, clip, video)
