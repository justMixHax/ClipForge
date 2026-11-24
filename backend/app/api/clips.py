"""Clips: review, edit decisions, captions, rendering, downloads, feedback."""
from __future__ import annotations

import copy
import logging
import re
import shutil
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..analysis.describe import clean_words, cover_words
from ..analysis.presets import CONTENT_PRESETS
from ..config import get_settings
from ..db import get_db
from ..editing import prompt_edit
from ..editing.captions import PRESETS as CAPTION_PRESETS
from ..editing.captions import CaptionWord, build_srt, build_vtt, mark_emphasis, words_for_clip
from ..editing.fonts import available_fonts
from ..editing.reframe import ASPECTS, LAYOUTS
from ..editing.thumbnail import can_draw, make_cover
from ..errors import ClipForgeError, Conflict, MediaError, NotFound
from ..jobs import pipeline, queue
from ..models import Clip, Feedback, Job, TranscriptSegment, Video
from ..schemas import (BatchIn, ClipList, ClipOut, ClipPatch, CoverIn, ExportIn, FeedbackIn, JobOut, PromptEditIn,
                       PromptEditOut, ReorderIn, TikTokCaptionIn, YouTubeTextIn)
from ..security.paths import safe_filename
from ..services import ffmpeg, hardware, settings_store, storage
from .common import clip_out, content_disposition, get_clip, get_video, job_out

router = APIRouter(prefix="/clips", tags=["clips"])
log = logging.getLogger("clipforge.api.clips")
NESTED = ("captions", "hook", "audio")
TOP_LEVEL = {"aspect", "layout", "subject_x", "subject_y", "zoom", "facecam_box", "gameplay_box", "captions", "hook",
             "audio", "remove_silence", "silence_threshold", "quality", "fps", "preset", "game_style", "cuts"}


def _cuts_ok(value: Any) -> bool:
    """Parts left out: [from, to] pairs in seconds of the source video."""
    return value is None or (isinstance(value, list) and len(value) <= 50 and all(
        isinstance(part, (list, tuple)) and len(part) == 2
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 for v in part)
        and part[0] < part[1] for part in value))


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
    if not _cuts_ok(merged.get("cuts")):
        fail("Parts to leave out must be pairs of times in seconds: from, to")
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
                                      min_gap=float(st.get("silence_threshold", 0.8)), cuts=st.get("cuts"))
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
        for extra, suffix in (("post.txt", ".txt"), ("captions.srt", ".srt"), ("thumbnail.jpg", ".jpg")):
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
    _keep_cuts_inside(clip)
    if "captions" in sent:
        clip.captions = None if body.captions is None else validate_captions(body.captions, clip.end - clip.start)
    if "tiktok_caption" in sent and body.tiktok_caption is not None:
        clip.tiktok_caption = body.tiktok_caption.replace("\r\n", "\n").strip()
    session.commit()
    session.refresh(clip)
    return clip_out(session, clip, video)


def _keep_cuts_inside(clip: Clip) -> None:
    """Parts left out always fit the clip, whatever was trimmed: those outside it are dropped (they would come
    back if it grew again), those across an end are trimmed to it, and a clip that would have nothing left to play
    is refused."""
    cuts = (clip.settings or {}).get("cuts")
    if not cuts:
        return
    fitted = [[a, b] for a, b in prompt_edit.fit_cuts(cuts, clip.start, clip.end)]
    if (clip.end - clip.start) - sum(b - a for a, b in fitted) < 0.3:
        raise ClipForgeError("That would leave nothing of the clip to play: all of it is in parts left out. Put "
                             "some back (they are listed under the editor's timeline) or make the clip longer.")
    if fitted != cuts:
        settings = {key: value for key, value in clip.settings.items() if key != "cuts"}
        clip.settings = {**settings, "cuts": fitted} if fitted else settings


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
                 cover_text=original.cover_text, cover_word=original.cover_word, cover_time=original.cover_time,
                 tiktok_caption=original.tiktok_caption,
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


@router.post("/{clip_id}/tiktok-caption")
def rewrite_tiktok_caption(clip_id: int, body: TikTokCaptionIn, session: Session = Depends(get_db)) -> dict:
    """Claude writes a new TikTok caption for the clip, with or without emojis and hashtags. Nothing is saved:
    change it as you like, then save it with the clip."""
    from ..publishing import assistant  # loaded only when asked for

    return {"caption": assistant.tiktok_caption(session, clip_id, emojis=body.emojis, hashtags=body.hashtags)}


@router.post("/{clip_id}/youtube-text")
def write_youtube_text(clip_id: int, body: YouTubeTextIn, session: Session = Depends(get_db)) -> dict:
    """Claude writes a YouTube Shorts title and description for the clip, to copy into YouTube Studio. Nothing
    is saved."""
    from ..publishing import assistant  # loaded only when asked for

    return assistant.youtube_text(session, clip_id, emojis=body.emojis, hashtags=body.hashtags, avoid=body.avoid)


_RENDERING = ("queued", "rendering")


@router.post("/{clip_id}/prompt-edit", response_model=PromptEditOut)
def prompt_edit_clip(clip_id: int, body: PromptEditIn, session: Session = Depends(get_db)) -> PromptEditOut:
    """Describe what you want changed: Claude turns it into changes ClipForge can make, they are checked within the
    editor's own limits, and the clip is rendered again. What cannot be done is said. Undo puts the clip back."""
    instruction = body.instruction.strip()
    if not instruction:
        raise ClipForgeError("Describe what you want changed in the clip.")
    clip = get_clip(session, clip_id)
    video = get_video(session, clip.video_id)
    if clip.render_status in _RENDERING:
        raise Conflict("This clip is being rendered. Describe your change when it is done.")
    source = storage.video_source_path(video)  # the source must still be here to render again
    cfg = settings_store.get_all(session)
    if cfg["ai.engine"] != "claude":
        raise ClipForgeError("Claude is switched off in Settings (local rules only), so nothing is sent to it.",
                             hint="Switch the AI engine to Claude in Settings to use prompt edits.")
    # Pictures of the video go with the request, so Claude sees where the people and cameras are.
    pictures = prompt_edit.pictures_of(source, clip.start, clip.end)
    frame = (video.width, video.height) if pictures.jpegs and video.width and video.height else None
    st = clip.settings or {}
    reach = prompt_edit.CONTEXT_SECONDS
    words = pipeline.load_words(session, video.id, max(0.0, clip.start - reach), clip.end + reach)
    sentences = [(row.start, row.end, row.text) for row in session.scalars(
        select(TranscriptSegment).where(TranscriptSegment.video_id == video.id,
                                        TranscriptSegment.end >= clip.start - reach,
                                        TranscriptSegment.start <= clip.end + reach).order_by(TranscriptSegment.idx))]
    segments = pipeline.clip_segments(clip.start, clip.end, words, bool(st.get("remove_silence")),
                                      min_gap=float(st.get("silence_threshold", 0.8)), cuts=st.get("cuts"))
    fonts = [font.name for font in available_fonts().values()]
    defaults = {"censor": bool(cfg["captions.censor"]), "normalize": bool(cfg["audio.normalize"])}
    prompt = prompt_edit.build_prompt(instruction, prompt_edit.snapshot(clip), video_title=video.title,
                                      creator=video.creator_name, duration=video.duration, sentences=sentences,
                                      segments=segments, fonts=fonts, defaults=defaults, frame=frame,
                                      picture_at=pictures.at, sheet=pictures.labels)
    session.commit()  # nothing held open while Claude thinks
    answer = prompt_edit.ask(prompt, str(cfg["ai.claude_model"]), pictures=pictures.jpegs if frame else ())

    session.refresh(clip)  # the clip as it is now: it may have been changed meanwhile
    if clip.render_status in _RENDERING:
        raise Conflict("This clip started rendering while Claude was answering, so nothing was changed. Try again "
                       "when it is done.")
    before = prompt_edit.snapshot(clip)
    snap = ((lambda box: prompt_edit.snap_part(pictures.grey, box, frame)) if frame and pictures.grey else None)
    outcome = prompt_edit.make_changes(before, answer.changes, duration=video.duration, words=words, fonts=fonts,
                                       defaults=defaults, frame=frame, snap=snap)
    done, not_done = list(outcome.done), [*answer.not_possible, *outcome.not_done]
    if outcome.done or outcome.fixes:
        outcome.state["settings"] = merge_settings(outcome.state["settings"], {})  # the editor's own checks too
        prompt_edit.restore(clip, outcome.state)
        if (clip.start, clip.end) != (before["start"], before["end"]):
            spoken = pipeline.load_words(session, clip.video_id, clip.start, clip.end)
            clip.transcript_text = " ".join(w.text for w in spoken
                                            if w.start >= clip.start - 0.01 and w.end <= clip.end + 0.01)
        if outcome.fixes:
            fixed, counts = prompt_edit.fix_words(clip_words(session, clip), outcome.fixes)
            for wrong, right in outcome.fixes:
                if counts[wrong]:
                    times = "once" if counts[wrong] == 1 else f"{counts[wrong]} times"
                    done.append(f"Subtitles: “{wrong}” → “{right}” ({times})")
                else:
                    not_done.append(f"“{wrong}” is not in the subtitles, so it was not changed.")
            if any(counts.values()):
                clip.captions = validate_captions([w.to_dict() for w in fixed], clip.end - clip.start)
    if not done:  # nothing to change after all: the clip stays exactly as it was
        session.rollback()
        session.refresh(clip)
        return PromptEditOut(summary=answer.summary or "Nothing was changed.", done=[], not_done=not_done,
                             rendering=False, clip=clip_out(session, clip, video))
    clip.last_edit = {"instruction": instruction, "summary": answer.summary, "done": done, "not_done": not_done,
                      "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "before": before}
    pipeline.enqueue_render(session, clip, priority=8)
    session.commit()
    session.refresh(clip)
    log.info("prompt edit made", extra={"clip_id": clip.id, "changes": len(done), "not_done": len(not_done)})
    return PromptEditOut(summary=answer.summary or "; ".join(done[:3]), done=done, not_done=not_done, rendering=True,
                         clip=clip_out(session, clip, video))


@router.post("/{clip_id}/prompt-edit/undo", response_model=ClipOut)
def undo_prompt_edit(clip_id: int, session: Session = Depends(get_db)) -> ClipOut:
    """Put the clip back as it was before its last prompt edit, and render it again."""
    clip = get_clip(session, clip_id)
    if clip.render_status in _RENDERING:
        raise Conflict("This clip is being rendered. Undo the prompt edit when it is done.")
    before = (clip.last_edit or {}).get("before")
    if not isinstance(before, dict):
        raise NotFound("There is nothing to undo: this clip has no prompt edit to take back.")
    video = get_video(session, clip.video_id)
    storage.video_source_path(video)
    prompt_edit.restore(clip, before)
    clip.last_edit = None
    pipeline.enqueue_render(session, clip, priority=8)
    session.commit()
    session.refresh(clip)
    log.info("prompt edit undone", extra={"clip_id": clip.id})
    return clip_out(session, clip, video)


_covers_made = threading.Lock()  # one thumbnail at a time: they take a second or two


@router.get("/{clip_id}/cover")
def clip_cover(clip_id: int, download: bool = False, session: Session = Depends(get_db)) -> FileResponse:
    clip = get_clip(session, clip_id)
    path = storage.data_path(clip.cover_path) if clip.cover_path else None
    if path is None or not path.is_file():
        raise NotFound("This clip has no thumbnail yet: it is made when the clip is rendered")
    name = f"{safe_filename(clip.title, default='clip', max_length=80)} - thumbnail.jpg"
    return FileResponse(path, media_type="image/jpeg", filename=name if download else None)


@router.post("/{clip_id}/cover", response_model=ClipOut)
def remake_cover(clip_id: int, body: CoverIn, session: Session = Depends(get_db)) -> ClipOut:
    """Change the thumbnail's words, the word in colour or its moment. A rendered clip gets the new thumbnail at
    once; before the render, your choice is kept for it."""
    clip = get_clip(session, clip_id)
    if clip.render_status in ("queued", "rendering"):
        raise Conflict("This clip is being rendered and gets its thumbnail at the end; change it when it is done.")
    if body.time is not None and not clip.start - 0.01 <= body.time <= clip.end + 0.01:
        raise ClipForgeError(f"Pick a moment inside the clip (between {clip.start:.1f} s and {clip.end:.1f} s of "
                             "the video).")
    if body.text is not None or body.word:
        text = body.text if body.text is not None else (clip.cover_text or cover_words(clip.title)[0])
        words, coloured = clean_words(text, body.word)
        if words and not can_draw(words):
            raise ClipForgeError("The fonts on this PC cannot draw these words yet (Arabic, Hebrew and similar scripts "
                                 "need text shaping that is not set up here). Use other words.")
        clip.cover_text, clip.cover_word = words, coloured
    if body.auto:
        at = None
    elif body.time is not None:
        at = round(min(max(body.time, clip.start), clip.end), 3)
    else:
        at = clip.cover_time if clip.cover_time is not None and clip.start <= clip.cover_time <= clip.end else None
    plan = (clip.render_info or {}).get("plan")
    if clip.render_status != "ready" or not plan:
        clip.cover_time = at  # kept for the render
        session.commit()
        return clip_out(session, clip)
    video = get_video(session, clip.video_id)
    source = storage.video_source_path(video)
    if not source.is_file():
        raise Conflict("The source video is no longer on this PC, so the thumbnail cannot be made again.")
    words, coloured = (clip.cover_text, clip.cover_word) if clip.cover_text else cover_words(clip.title)
    folder = storage.clip_dir(clip)
    with _covers_made:
        try:
            picture, moment = make_cover(source, clip.start, clip.end, plan, (video.width, video.height), words,
                                         coloured, folder, at=at, inside=(clip.render_info or {}).get("segments"))
        except (ffmpeg.FFmpegError, OSError) as exc:
            raise MediaError(f"The thumbnail could not be made: {exc}") from exc
    clip.cover_path, clip.cover_time = storage.rel_path(picture), moment
    session.commit()
    if (folder / "post.txt").exists():
        (folder / "post.txt").write_text(pipeline.build_post_kit(clip, video), encoding="utf-8")
    return clip_out(session, clip)


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
