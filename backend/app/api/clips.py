"""Clips: review, edit decisions, captions, rendering, downloads, feedback."""
from __future__ import annotations

import copy
import re
import shutil
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..analysis.presets import CONTENT_PRESETS
from ..config import get_settings
from ..db import get_db
from ..editing.captions import PRESETS as CAPTION_PRESETS
from ..editing.captions import CaptionWord, build_srt, build_vtt, mark_emphasis, words_for_clip
from ..editing.reframe import ASPECTS, LAYOUTS
from ..errors import ClipForgeError, NotFound
from ..jobs import pipeline, queue
from ..models import Clip, Feedback, Job, Video
from ..schemas import (BatchIn, ClipList, ClipOut, ClipPatch, ExportIn, FeedbackIn, JobOut, ReorderIn)
from ..security.paths import safe_filename
from ..services import hardware, settings_store, storage
from .common import clip_out, content_disposition, get_clip, get_video, job_out

router = APIRouter(prefix="/clips", tags=["clips"])
NESTED = ("captions", "hook", "audio")
TOP_LEVEL = {"aspect", "layout", "subject_x", "subject_y", "zoom", "facecam_box", "gameplay_box", "captions", "hook",
             "audio", "remove_silence", "silence_threshold", "quality", "fps", "preset", "game_style"}


def _box_ok(value: Any) -> bool:
    return value is None or (isinstance(value, (list, tuple)) and len(value) == 4
                             and all(isinstance(v, (int, float)) and v >= 0 for v in value) and value[2] > 0 and value[3] > 0)


def merge_settings(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Apply a partial change to a clip's edit settings, rejecting values the renderer would not accept."""
    unknown = set(patch) - TOP_LEVEL
    if unknown:
        raise ClipForgeError(f"Unknown edit setting: {', '.join(sorted(unknown))}")
    merged = copy.deepcopy(current or {})
    for key, value in patch.items():
        if key in NESTED:
            if not isinstance(value, dict):
                raise ClipForgeError(f"“{key}” must be a group of settings")
            merged[key] = {**(merged.get(key) or {}), **value}
        else:
            merged[key] = value

    def fail(message: str) -> None:
        raise ClipForgeError(message)

    if merged.get("aspect") not in ASPECTS:
        fail("Unknown aspect ratio")
    if merged.get("layout") not in LAYOUTS:
        fail("Unknown layout")
    for axis in ("subject_x", "subject_y"):
        v = merged.get(axis)
        if v is not None and not (isinstance(v, (int, float)) and 0 <= v <= 1):
            fail("The subject position must be between 0 and 1")
    if not (isinstance(merged.get("zoom", 1.0), (int, float)) and 1.0 <= merged.get("zoom", 1.0) <= 3.0):
        fail("Zoom must be between 1 and 3")
    if not _box_ok(merged.get("facecam_box")) or not _box_ok(merged.get("gameplay_box")):
        fail("A box must be four numbers: x, y, width, height")
    if merged.get("quality", "balanced") not in hardware.QUALITIES:
        fail("Unknown quality setting")
    if merged.get("fps", 30) not in (24, 25, 30, 50, 60):
        fail("Frame rate must be 24, 25, 30, 50 or 60")
    if merged.get("preset", "auto") not in CONTENT_PRESETS:
        fail("Unknown content preset")
    threshold = merged.get("silence_threshold", 0.8)
    if not (isinstance(threshold, (int, float)) and 0.2 <= threshold <= 5):
        fail("The silence threshold must be between 0.2 and 5 seconds")
    captions = merged.get("captions") or {}
    if captions.get("preset", "opus") not in CAPTION_PRESETS:
        fail("Unknown caption style")
    hook = merged.get("hook") or {}
    if not (isinstance(hook.get("seconds", 4.0), (int, float)) and 0 <= hook.get("seconds", 4.0) <= 600):
        fail("The hook duration must be between 0 and 600 seconds")
    return merged


def validate_captions(raw: list[dict[str, Any]], clip_seconds: float) -> list[dict[str, Any]]:
    if len(raw) > 5000:
        raise ClipForgeError("Too many caption words")
    cleaned = []
    for item in raw:
        text = str(item.get("text", "")).strip()
        start, end = item.get("start"), item.get("end")
        if not text or len(text) > 80:
            raise ClipForgeError("Each caption word needs text of at most 80 characters")
        if not (isinstance(start, (int, float)) and isinstance(end, (int, float)) and 0 <= start < end
                and end <= clip_seconds + 1.0):
            raise ClipForgeError(f"The timing of “{text}” is not valid for this clip")
        cleaned.append(CaptionWord(text=text, start=round(float(start), 3), end=round(float(end), 3),
                                   emph=bool(item.get("emph", False)), locked=bool(item.get("locked", True)),
                                   hidden=bool(item.get("hidden", False))).to_dict())
    return sorted(cleaned, key=lambda w: w["start"])


def clip_words(session: Session, clip: Clip) -> list[CaptionWord]:
    """The caption words of a clip as the renderer will use them: edited ones if any, else from the transcript."""
    if clip.captions is not None:
        return [CaptionWord.from_dict(d) for d in clip.captions]
    words = pipeline.load_words(session, clip.video_id, clip.start - 2.0, clip.end + 2.0)
    st = clip.settings or {}
    segments = pipeline.clip_segments(clip.start, clip.end, words, bool(st.get("remove_silence")),
                                      min_gap=float(st.get("silence_threshold", 0.8)))
    out = words_for_clip(words, segments)
    mark_emphasis(out)
    return out


@router.get("", response_model=ClipList)
def list_clips(video_id: int | None = None, project_id: int | None = None, review_status: str | None = None,
               render_status: str | None = None, favorite: bool | None = None, origin: str | None = None,
               q: str | None = Query(None, max_length=200), min_score: float | None = None,
               sort: str = Query("score", pattern="^(score|rank|newest|oldest|duration|source)$"),
               limit: int = Query(200, ge=1, le=500), offset: int = Query(0, ge=0),
               session: Session = Depends(get_db)) -> ClipList:
    query = select(Clip)
    if video_id is not None:
        query = query.where(Clip.video_id == video_id)
    if project_id is not None:
        query = query.where(Clip.project_id == project_id)
    if review_status:
        query = query.where(Clip.review_status == review_status)
    if render_status:
        query = query.where(Clip.render_status == render_status)
    if favorite is not None:
        query = query.where(Clip.favorite == favorite)
    if origin:
        query = query.where(Clip.origin == origin)
    if min_score is not None:
        query = query.where(Clip.score >= min_score)
    if q:
        like = f"%{q.strip()}%"
        query = query.where(Clip.title.ilike(like) | Clip.transcript_text.ilike(like) | Clip.category.ilike(like))
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    order = {
        "score": (Clip.score.desc(), Clip.id.desc()),
        # your own order first (lowest number on top), then the AI's score
        "rank": (func.coalesce(Clip.manual_rank, 1_000_000).asc(), Clip.score.desc(), Clip.id.asc()),
        "newest": (Clip.created_at.desc(), Clip.id.desc()), "oldest": (Clip.created_at.asc(), Clip.id.asc()),
        "duration": ((Clip.end - Clip.start).desc(),), "source": (Clip.video_id.asc(), Clip.start.asc()),
    }[sort]
    rows = list(session.scalars(query.order_by(*order).limit(limit).offset(offset)))
    videos = {v.id: v for v in session.scalars(select(Video).where(Video.id.in_({c.video_id for c in rows})))} if rows else {}
    return ClipList(items=[clip_out(session, c, videos.get(c.video_id)) for c in rows], total=total)


@router.post("/batch")
def batch(body: BatchIn, session: Session = Depends(get_db)) -> dict:
    changed = 0
    for clip in session.scalars(select(Clip).where(Clip.id.in_(body.ids))):
        if body.action in ("approve", "reject", "pending"):
            clip.review_status = {"approve": "approved", "reject": "rejected", "pending": "pending"}[body.action]
        elif body.action in ("favorite", "unfavorite"):
            clip.favorite = body.action == "favorite"
        elif body.action == "render":
            pipeline.enqueue_render(session, clip)
        elif body.action == "delete":
            _delete(session, clip)
        changed += 1
    session.commit()
    return {"changed": changed}


@router.post("/reorder")
def reorder(body: ReorderIn, session: Session = Depends(get_db)) -> dict:
    """Set your own ranking: the given clips, in this order, go to the top of the list."""
    rows = {c.id: c for c in session.scalars(select(Clip).where(Clip.id.in_(body.ids)))}
    for position, clip_id in enumerate(body.ids, 1):
        if clip_id in rows:
            rows[clip_id].manual_rank = position
    session.commit()
    return {"ordered": len(rows)}


@router.post("/reset-order")
def reset_order(video_id: int | None = None, session: Session = Depends(get_db)) -> dict:
    query = select(Clip).where(Clip.manual_rank.is_not(None))
    if video_id is not None:
        query = query.where(Clip.video_id == video_id)
    rows = list(session.scalars(query))
    for clip in rows:
        clip.manual_rank = None
    session.commit()
    return {"reset": len(rows)}


@router.post("/export")
def export_clips(body: ExportIn, session: Session = Depends(get_db)) -> dict:
    """Copy finished clips and their post text to the export folder, numbered and named by title."""
    configured = str(settings_store.get(session, "export.folder") or "").strip()
    if configured:
        folder = Path(configured)
        if not folder.is_absolute() or configured.startswith("\\\\"):
            raise ClipForgeError("The export folder in Settings must be a full local path, for example D:\\Clips")
    else:
        folder = get_settings().exports_dir / date.today().isoformat()
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ClipForgeError(f"The export folder could not be created: {exc.strerror or exc}") from exc
    taken = [int(m.group(1)) for p in folder.glob("*.mp4") if (m := re.match(r"(\d+) - ", p.name))]
    number = max(taken, default=0)
    files, skipped = [], []
    for clip in session.scalars(select(Clip).where(Clip.id.in_(body.ids)).order_by(Clip.score.desc())):
        if clip.render_status != "ready" or not clip.file_path:
            skipped.append(clip.title)
            continue
        number += 1
        stem = f"{number:02d} - {safe_filename(clip.title, default='clip', max_length=80)}"
        source = storage.data_path(clip.file_path)
        shutil.copyfile(source, folder / f"{stem}.mp4")
        for extra, suffix in (("post.txt", ".txt"), ("captions.srt", ".srt")):
            if (source.parent / extra).exists():
                shutil.copyfile(source.parent / extra, folder / f"{stem}{suffix}")
        files.append(f"{stem}.mp4")
    return {"folder": str(folder), "files": files, "skipped": skipped}


@router.get("/{clip_id}", response_model=ClipOut)
def read_clip(clip_id: int, session: Session = Depends(get_db)) -> ClipOut:
    return clip_out(session, get_clip(session, clip_id))


@router.patch("/{clip_id}", response_model=ClipOut)
def update_clip(clip_id: int, body: ClipPatch, session: Session = Depends(get_db)) -> ClipOut:
    clip = get_clip(session, clip_id)
    video = get_video(session, clip.video_id)
    sent = body.model_fields_set
    start = body.start if "start" in sent and body.start is not None else clip.start
    end = body.end if "end" in sent and body.end is not None else clip.end
    if "start" in sent or "end" in sent:
        if end - start < 0.3:
            raise ClipForgeError("A clip must be at least 0.3 seconds long, and end after it starts.")
        if end > video.duration + 0.05:
            raise ClipForgeError(f"The source video is only {video.duration:.1f} seconds long.")
        if (round(start, 3), round(end, 3)) != (clip.start, clip.end):
            clip.start, clip.end = round(start, 3), round(end, 3)
            words = pipeline.load_words(session, clip.video_id, clip.start, clip.end)
            clip.transcript_text = " ".join(w.text for w in words if w.start >= clip.start - 0.01 and w.end <= clip.end + 0.01)
            if "captions" not in sent:
                clip.captions = None  # hand-edited caption timings no longer match the new cut
    for field in ("title", "hook_text", "description", "review_status", "favorite"):
        if field in sent and getattr(body, field) is not None:
            setattr(clip, field, getattr(body, field))
    if "hashtags" in sent and body.hashtags is not None:
        clip.hashtags = [("#" + t.strip().lstrip("#").replace(" ", ""))[:60] for t in body.hashtags if t.strip().lstrip("#")]
    if "manual_rank" in sent:
        clip.manual_rank = body.manual_rank
    if "settings" in sent and body.settings is not None:
        clip.settings = merge_settings(clip.settings, body.settings)
    if "captions" in sent:
        clip.captions = None if body.captions is None else validate_captions(body.captions, clip.end - clip.start)
    session.commit()
    session.refresh(clip)
    return clip_out(session, clip, video)


def _delete(session: Session, clip: Clip) -> None:
    for job in session.scalars(select(Job).where(Job.clip_id == clip.id, Job.status.in_(queue.ACTIVE))):
        queue.request_cancel(session, job.id)
    storage.remove_tree(get_settings().clips_dir / clip.storage_key)
    session.delete(clip)


@router.delete("/{clip_id}")
def delete_clip(clip_id: int, session: Session = Depends(get_db)) -> dict:
    _delete(session, get_clip(session, clip_id))
    session.commit()
    return {"deleted": True}


@router.post("/{clip_id}/render", response_model=JobOut)
def render(clip_id: int, session: Session = Depends(get_db)) -> JobOut:
    clip = get_clip(session, clip_id)
    storage.video_source_path(get_video(session, clip.video_id))
    return job_out(pipeline.enqueue_render(session, clip, priority=8))  # asked for by hand: ahead of the queue


@router.post("/{clip_id}/duplicate", response_model=ClipOut)
def duplicate(clip_id: int, label: str = Query("Variant", max_length=80), session: Session = Depends(get_db)) -> ClipOut:
    """Make a copy to try a different hook, caption style or cut (an A/B variant). Nothing is rendered yet."""
    original = get_clip(session, clip_id)
    copy_ = Clip(video_id=original.video_id, project_id=original.project_id, storage_key=storage.new_key(),
                 origin="variant", title=original.title, hook_text=original.hook_text,
                 hook_options=list(original.hook_options or []), description=original.description,
                 hashtags=list(original.hashtags or []), category=original.category,
                 transcript_text=original.transcript_text, start=original.start, end=original.end,
                 score=original.score, subscores=dict(original.subscores or {}), confidence=original.confidence,
                 explanation=original.explanation, evidence=list(original.evidence or []),
                 analysis_source=original.analysis_source, rank=original.rank,
                 settings=copy.deepcopy(original.settings or {}), captions=copy.deepcopy(original.captions),
                 variant_of=original.variant_of or original.id, variant_label=label)
    session.add(copy_)
    session.commit()
    session.refresh(copy_)
    return clip_out(session, copy_)


@router.get("/{clip_id}/file")
def clip_file(clip_id: int, download: bool = False, session: Session = Depends(get_db)) -> FileResponse:
    clip = get_clip(session, clip_id)
    if not clip.file_path:
        raise NotFound("This clip has not been rendered yet")
    path = storage.data_path(clip.file_path)  # during a re-render this is still the previous, complete file
    if not path.is_file():
        raise NotFound("The rendered file is missing; render the clip again")
    name = f"{safe_filename(clip.title, default='clip', max_length=80)}.mp4"
    return FileResponse(path, media_type="video/mp4", filename=name if download else None)


@router.get("/{clip_id}/thumbnail")
def clip_thumbnail(clip_id: int, session: Session = Depends(get_db)) -> FileResponse:
    clip = get_clip(session, clip_id)
    if not clip.thumb_path or not storage.data_path(clip.thumb_path).is_file():
        raise NotFound("No cover frame yet")
    return FileResponse(storage.data_path(clip.thumb_path), media_type="image/jpeg")


@router.get("/{clip_id}/captions")
def read_captions(clip_id: int, session: Session = Depends(get_db)) -> dict:
    clip = get_clip(session, clip_id)
    return {"edited": clip.captions is not None, "words": [w.to_dict() for w in clip_words(session, clip)]}


@router.get("/{clip_id}/subtitles.{fmt}")
def subtitles(clip_id: int, fmt: str, session: Session = Depends(get_db)) -> PlainTextResponse:
    """SRT or VTT caption file. Always verbatim: on-screen masking of swear words does not apply here."""
    if fmt not in ("srt", "vtt"):
        raise NotFound("Captions are available as .srt or .vtt")
    clip = get_clip(session, clip_id)
    words = clip_words(session, clip)
    text = build_srt(words) if fmt == "srt" else build_vtt(words)
    name = f"{safe_filename(clip.title, default='clip', max_length=80)}.{fmt}"
    return PlainTextResponse(text, media_type="text/vtt" if fmt == "vtt" else "application/x-subrip",
                             headers={"Content-Disposition": content_disposition(name)})


@router.get("/{clip_id}/post-kit")
def post_kit(clip_id: int, session: Session = Depends(get_db)) -> PlainTextResponse:
    clip = get_clip(session, clip_id)
    return PlainTextResponse(pipeline.build_post_kit(clip, get_video(session, clip.video_id)))


@router.post("/{clip_id}/feedback")
def give_feedback(clip_id: int, body: FeedbackIn, session: Session = Depends(get_db)) -> dict:
    """Record what you thought of a clip. Future rankings shift accordingly (see Settings for the effect)."""
    clip = get_clip(session, clip_id)
    session.add(Feedback(clip_id=clip.id, label=body.label, note=body.note,
                         snapshot={"category": clip.category, "score": clip.score, "subscores": clip.subscores,
                                   "duration": round(clip.end - clip.start, 1),
                                   "caption_preset": ((clip.settings or {}).get("captions") or {}).get("preset"),
                                   "layout": (clip.render_info or {}).get("layout")}))
    session.commit()
    return {"stored": True}
