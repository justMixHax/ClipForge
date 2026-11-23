"""Everything that is not a video, clip or job: health, hardware, settings, dashboard, search, projects."""
from __future__ import annotations

import logging
import threading
import time
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .. import __version__
from ..analysis.feedback import FeedbackProfile
from ..analysis.lexicon import content_tokens
from ..analysis.presets import (CONTENT_PRESETS, DIMENSION_LABELS, DIMENSIONS, GAME_STYLES, LENGTH_RANGES,
                                WORKFLOW_PRESETS)
from ..analysis.scoring import CATEGORY_LABELS
from ..config import get_settings
from ..db import get_db, utcnow
from ..editing.captions import PRESET_LABELS, PRESETS
from ..editing.fonts import available_fonts
from ..editing.reframe import ASPECTS, LAYOUT_LABELS
from ..errors import ClipForgeError, Conflict, NotFound
from ..jobs import queue
from ..models import FEEDBACK_LABELS, Account, Clip, Feedback, Job, Notification, Post, Preset, Project, Video
from ..schemas import (SAVED_OPTIONS, AnalyzeIn, CleanupIn, PresetIn, PresetOut, ProjectIn, ProjectOut, ProjectPatch,
                       SecretIn, SettingsIn)
from ..services import hardware, settings_store, storage
from ..services.features import feature_status
from ..transcription import models as speech_models
from .common import clip_out, job_out

router = APIRouter(tags=["system"])
log = logging.getLogger("clipforge.api.system")
SEARCH_NOISE = {"find", "moment", "moments", "where", "when", "part", "clip", "clips", "show", "streamer", "video",
                "starts", "start", "says", "said", "talks", "talking", "something", "someone", "after", "before"}
_installs: dict[str, dict[str, Any]] = {}


@router.get("/health")
def health(request: Request) -> dict:
    supervisor = getattr(request.app.state, "worker", None)
    return {"status": "ok", "version": __version__, "worker": supervisor.status() if supervisor else {"managed": False}}


# ---------------------------------------------------------------------------------------------- hardware
@router.post("/system/shutdown")
def shut_down(session: Session = Depends(get_db)) -> dict:
    """Stop ClipForge on this PC from the app, the same orderly way as scripts/stop.ps1: no new requests, and a
    job that is running goes back in the queue to continue the next time it starts."""
    from .. import serve

    jobs = session.scalar(select(func.count(Job.id)).where(Job.status.in_(queue.ACTIVE))) or 0
    if not serve.request_stop():
        raise Conflict("This copy of ClipForge was not started with start.ps1 or its desktop shortcut, so it cannot "
                       "stop itself from here. Close the window it runs in.")
    log.info("shutdown asked for from the app", extra={"jobs": jobs})
    return {"stopping": True, "jobs": jobs}


@router.get("/system/hardware")
def read_hardware(session: Session = Depends(get_db)) -> dict:
    hw = hardware.detect()
    installed = speech_models.installed_models()
    return {"hardware": hw, "recommended": hardware.recommend(hw, settings_store.get(session, "processing.priority"),
                                                              installed)}


@router.post("/system/hardware/refresh")
def refresh_hardware(session: Session = Depends(get_db)) -> dict:
    """Run the encoder and GPU tests again (takes a few seconds)."""
    hw = hardware.detect(force=True)
    return {"hardware": hw, "recommended": hardware.recommend(hw, settings_store.get(session, "processing.priority"),
                                                              speech_models.installed_models())}


@router.get("/system/stats")
def live_stats(session: Session = Depends(get_db)) -> dict:
    usage = hardware.live_usage()
    running = session.scalars(select(Job).where(Job.status == "running").order_by(Job.id)).first()
    return {**usage, "queue_length": session.scalar(select(func.count(Job.id)).where(Job.status == "queued")) or 0,
            "running": job_out(running) if running else None}


@router.post("/system/ai/test")
def test_ai_connections(session: Session = Depends(get_db)) -> dict:
    """Send a one-word test to Claude and to ChatGPT, at the same time, and say how each went."""
    from concurrent.futures import ThreadPoolExecutor

    from ..analysis import chatgpt_review, claude_review

    cfg = settings_store.get_all(session)

    def check(name: str, run, model: str) -> dict:  # noqa: ANN001
        started = time.monotonic()
        try:
            reply = run(model)
            return {"ok": True, "detail": f"Answered “{reply}”", "model": model,
                    "seconds": round(time.monotonic() - started, 1)}
        except (claude_review.ClaudeUnavailable, chatgpt_review.ChatGPTUnavailable) as exc:
            return {"ok": False, "detail": exc.message + (f" {exc.hint}" if exc.hint else ""), "model": model,
                    "seconds": round(time.monotonic() - started, 1)}

    with ThreadPoolExecutor(max_workers=2) as pool:
        claude = pool.submit(check, "claude", claude_review.ping, "haiku")
        chatgpt = pool.submit(check, "chatgpt", chatgpt_review.ping, str(cfg["ai.chatgpt_model"]))
        return {"claude": claude.result(), "chatgpt": chatgpt.result()}


@router.get("/system/ai/chatgpt-models")
def chatgpt_models() -> dict:
    """The ChatGPT models this PC's ChatGPT sign-in was last offered (from the Codex tool's own list)."""
    from ..analysis import chatgpt_review

    return {"offered": chatgpt_review.offered_models()}


@router.get("/system/features")
def features(session: Session = Depends(get_db)) -> list[dict]:
    return feature_status(session)


@router.get("/system/storage")
def read_storage() -> dict:
    return storage.usage()


@router.post("/system/cleanup")
def cleanup(body: CleanupIn, session: Session = Depends(get_db)) -> dict:
    """Free disk space safely. Nothing here touches source videos, approved clips or anything outside the
    data folder."""
    settings = get_settings()
    freed = 0
    if body.what == "work":
        # intermediate files (extracted audio, signals) of videos that are not being processed right now.
        # Transcripts stay in the database; re-analysing later re-creates what is needed.
        busy = {v for v in session.scalars(select(Job.video_id).where(Job.status.in_(queue.ACTIVE))) if v}
        keys = {v.storage_key: v.id for v in session.scalars(select(Video))}
        for folder in settings.work_dir.iterdir() if settings.work_dir.exists() else []:
            if folder.is_dir() and keys.get(folder.name) not in busy:
                for name in ("audio.wav", "audio.ok", "audio.npz", "visual.npz", "transcript.partial.json"):
                    target = folder / name
                    if target.exists():
                        freed += target.stat().st_size
                        target.unlink()
    elif body.what == "rejected":
        for clip in session.scalars(select(Clip).where(Clip.review_status == "rejected", Clip.file_path != "")):
            freed += storage.remove_tree(settings.clips_dir / clip.storage_key)
            clip.file_path, clip.thumb_path, clip.file_size, clip.render_status = "", "", 0, "none"
            clip.cover_path = ""
    else:  # failed_jobs
        finished = list(session.scalars(select(Job).where(Job.status.in_(("failed", "cancelled", "succeeded")),
                                                          Job.finished_at < utcnow() - timedelta(days=7))))
        for job in finished:
            session.delete(job)
    session.commit()
    return {"freed_bytes": freed}


# ---------------------------------------------------------------------------------------------- settings
def _settings_body(session: Session) -> dict:
    labels = [(f.label, (f.snapshot or {}).get("category", "")) for f in session.scalars(select(Feedback))]
    profile = FeedbackProfile.from_labels(labels)
    return {"values": settings_store.get_all(session), "secrets": settings_store.secrets_present(session),
            "feedback": {"count": len(labels), "counts": profile.counts, "effects": profile.summary()}}


@router.get("/settings")
def read_settings(session: Session = Depends(get_db)) -> dict:
    """Settings plus which credentials are stored. Credential values are never included."""
    return _settings_body(session)


@router.put("/settings")
def write_settings(body: SettingsIn, session: Session = Depends(get_db)) -> dict:
    settings_store.set_many(session, body.values)
    return _settings_body(session)


@router.put("/settings/secret")
def write_secret(body: SecretIn, session: Session = Depends(get_db)) -> dict:
    """Store (or, with an empty value, remove) a credential. It is encrypted and cannot be read back."""
    settings_store.set_secret(session, body.name, body.value)
    return {"stored": bool(body.value.strip())}


@router.get("/options")
def options() -> dict:
    """The choices the interface offers, so they are defined in one place (the backend)."""
    return {
        "content_presets": [{"key": k, "label": v["label"], "description": v["description"], "captions": v["captions"],
                             "layout": v["layout"]} for k, v in CONTENT_PRESETS.items()],
        "workflow_presets": [{"key": k, **v} for k, v in WORKFLOW_PRESETS.items()],
        "game_styles": [{"key": k, "label": v["label"]} for k, v in GAME_STYLES.items()],
        "length_ranges": [{"key": k, "min": a, "max": b, "label": f"{a:g}–{b:g} s"} for k, (a, b) in LENGTH_RANGES.items()],
        "aspects": [{"key": k, "width": w, "height": h} for k, (w, h) in ASPECTS.items()],
        "layouts": [{"key": k, "label": v} for k, v in LAYOUT_LABELS.items()],
        "caption_presets": [{"key": k, "label": PRESET_LABELS[k], "style": {
            "font": p.font, "size": p.size, "color": p.color, "highlight": p.highlight, "emphasis": p.emphasis,
            "uppercase": p.uppercase, "max_words": p.max_words, "animation": p.animation, "box": p.box,
            "outline": p.outline, "position": p.position}} for k, p in PRESETS.items()],
        "fonts": [{"name": f.name, "source": f.source} for f in available_fonts().values()],
        "dimensions": [{"key": d, "label": DIMENSION_LABELS[d]} for d in DIMENSIONS],
        "categories": CATEGORY_LABELS,
        "feedback_labels": list(FEEDBACK_LABELS),
        "speech_models": [{"key": k, **v, "installed": speech_models.is_installed(k),
                           "on_disk_elsewhere": not speech_models.is_installed(k)
                           and speech_models.find_in_local_caches(k) is not None,
                           "install": _installs.get(k)} for k, v in speech_models.WHISPER_MODELS.items()],
        "max_upload_gb": get_settings().max_upload_gb,
    }


@router.post("/models/{name}/install")
def install_model(name: str) -> dict:
    """Install a speech model: copied from a cache already on this PC when possible, otherwise downloaded
    from Hugging Face. This is the only place the app downloads anything, and only when asked."""
    if name not in speech_models.WHISPER_MODELS:
        raise NotFound("Unknown speech model")
    if speech_models.is_installed(name):
        return {"state": "installed"}
    if _installs.get(name, {}).get("state") == "running":
        return _installs[name]
    _installs[name] = {"state": "running", "message": "Starting…"}

    def work() -> None:
        try:
            if speech_models.install_from_cache(name):
                _installs[name] = {"state": "installed", "message": "Copied from a model cache already on this PC."}
                return
            _installs[name]["message"] = (f"Downloading about {speech_models.WHISPER_MODELS[name]['size_mb']} MB "
                                          "from Hugging Face…")
            speech_models.download(name)
            ok = speech_models.is_installed(name)
            _installs[name] = {"state": "installed" if ok else "failed",
                               "message": "Downloaded." if ok else "The download finished but files are missing."}
        except Exception as exc:  # noqa: BLE001
            log.error("model install failed", exc_info=True)
            _installs[name] = {"state": "failed", "message": f"Install failed: {exc}"}

    threading.Thread(target=work, daemon=True, name=f"install-{name}").start()
    return _installs[name]


# --------------------------------------------------------------------------------------------- dashboard
@router.get("/dashboard")
def dashboard(session: Session = Depends(get_db)) -> dict:
    """Numbers for the home page. All of them are counted from this PC's database; nothing is estimated
    except where the label says so."""
    def count(model, *conditions) -> int:  # noqa: ANN001
        return session.scalar(select(func.count(model.id)).where(*conditions)) or 0

    week = utcnow() - timedelta(days=7)
    finished = list(session.execute(
        select(Job.type, Job.started_at, Job.finished_at).where(Job.status == "succeeded", Job.finished_at >= week,
                                                                Job.started_at.is_not(None))))
    seconds = {"analyze": 0.0, "render": 0.0}
    for kind, started, ended in finished:
        seconds[kind] = seconds.get(kind, 0.0) + max(0.0, (ended - started).total_seconds())
    top = list(session.scalars(select(Clip).where(Clip.review_status != "rejected")
                               .order_by(Clip.score.desc(), Clip.id.desc()).limit(6)))
    recent = list(session.scalars(select(Job).order_by(Job.id.desc()).limit(8)))
    return {
        "videos": {"total": count(Video), "analyzed": count(Video, Video.status == "analyzed"),
                   "hours": round((session.scalar(select(func.sum(Video.duration))) or 0) / 3600, 2)},
        "clips": {"total": count(Clip), "awaiting_review": count(Clip, Clip.review_status == "pending"),
                  "approved": count(Clip, Clip.review_status == "approved"),
                  "rejected": count(Clip, Clip.review_status == "rejected"),
                  "rendered": count(Clip, Clip.render_status == "ready"), "favorites": count(Clip, Clip.favorite),
                  "average_score": round(session.scalar(select(func.avg(Clip.score)).where(Clip.origin == "ai")) or 0, 1)},
        # Counted from the post queue. Views are not read from the platforms yet (Analytics is not built), so
        # they stay empty rather than showing made-up numbers.
        "publishing": {"connected_accounts": count(Account, Account.status == "connected"),
                       "scheduled": count(Post, Post.status.in_(("scheduled", "scheduled_on_platform"))),
                       "published": count(Post, Post.status == "published"),
                       "in_drafts": count(Post, Post.status == "in_drafts"),
                       "failed": count(Post, Post.status == "failed"), "verified_views": None,
                       "note": "Views and engagement are not read from the platforms yet."},
        "queue": {"waiting": count(Job, Job.status == "queued"), "running": count(Job, Job.status == "running"),
                  "failed_this_week": count(Job, Job.status == "failed", Job.created_at >= week)},
        "compute": {"analyze_seconds_7d": round(seconds["analyze"]), "render_seconds_7d": round(seconds["render"]),
                    "jobs_7d": len(finished)},
        "top_clips": [clip_out(session, c) for c in top],
        "recent_jobs": [job_out(j) for j in recent],
        "storage": storage.usage(),
    }


# ----------------------------------------------------------------------------------------- notifications
@router.get("/notifications")
def notifications(limit: int = Query(30, ge=1, le=100), session: Session = Depends(get_db)) -> dict:
    rows = list(session.scalars(select(Notification).order_by(Notification.id.desc()).limit(limit)))
    unread = session.scalar(select(func.count(Notification.id)).where(Notification.read.is_(False))) or 0
    return {"unread": unread, "items": [{"id": n.id, "kind": n.kind, "title": n.title, "body": n.body, "link": n.link,
                                         "read": n.read, "created_at": n.created_at} for n in rows]}


@router.post("/notifications/read")
def mark_read(session: Session = Depends(get_db)) -> dict:
    session.query(Notification).filter(Notification.read.is_(False)).update({Notification.read: True})
    session.commit()
    return {"unread": 0}


# ------------------------------------------------------------------------------------------------ search
@router.get("/search")
def search(q: str = Query(min_length=1, max_length=300), video_id: int | None = None,
           session: Session = Depends(get_db)) -> dict:
    """Search inside videos by what was said. Keyword-based: filler words in the question are ignored and
    word endings are matched loosely ("losing" finds "lost" only if both share a stem)."""
    tokens = [t for t in content_tokens(q) if t not in SEARCH_NOISE] or [t for t in content_tokens(q)]
    tokens = [t.replace('"', "") for t in tokens if len(t.replace('"', "")) >= 2][:12]
    if not tokens:
        return {"query": q, "terms": [], "hits": []}
    match = " OR ".join(f'"{t}"*' for t in tokens)
    sql = ("SELECT s.id, s.video_id, s.idx, s.start, s.end, s.text, bm25(transcript_fts) AS rank "
           "FROM transcript_fts JOIN transcript_segments s ON s.id = transcript_fts.rowid "
           "WHERE transcript_fts MATCH :match" + (" AND s.video_id = :video_id" if video_id is not None else "")
           + " ORDER BY rank LIMIT 40")
    params: dict[str, Any] = {"match": match}
    if video_id is not None:
        params["video_id"] = video_id
    try:
        rows = session.execute(text(sql), params).all()
    except Exception as exc:  # noqa: BLE001
        raise ClipForgeError("That search could not be run; try simpler words.") from exc
    titles = {v.id: (v.title, v.duration) for v in session.scalars(
        select(Video).where(Video.id.in_({r.video_id for r in rows})))} if rows else {}
    hits = []
    for r in rows:
        title, duration = titles.get(r.video_id, ("", 0.0))
        hits.append({"segment_id": r.id, "video_id": r.video_id, "video_title": title, "start": r.start, "end": r.end,
                     "text": r.text,
                     # a ready-made clip range around the line: a little lead-in, and room for the reaction
                     "clip_start": round(max(0.0, r.start - 6.0), 2),
                     "clip_end": round(min(duration or r.end + 12.0, r.end + 12.0), 2)})
    return {"query": q, "terms": tokens, "hits": hits}


# ---------------------------------------------------------------------------------------------- projects
def _project_out(session: Session, project: Project) -> ProjectOut:
    out = ProjectOut.model_validate(project)
    out.video_count = session.scalar(select(func.count(Video.id)).where(Video.project_id == project.id)) or 0
    out.clip_count = session.scalar(select(func.count(Clip.id)).where(Clip.project_id == project.id)) or 0
    return out


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(session: Session = Depends(get_db)) -> list[ProjectOut]:
    return [_project_out(session, p) for p in session.scalars(select(Project).order_by(Project.name))]


@router.post("/projects", response_model=ProjectOut)
def create_project(body: ProjectIn, session: Session = Depends(get_db)) -> ProjectOut:
    project = Project(name=body.name, description=body.description)
    session.add(project)
    session.commit()
    session.refresh(project)
    return _project_out(session, project)


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def update_project(project_id: int, body: ProjectPatch, session: Session = Depends(get_db)) -> ProjectOut:
    project = session.get(Project, project_id)
    if project is None:
        raise NotFound("That project does not exist")
    if body.name is not None:
        project.name = body.name
    if body.description is not None:
        project.description = body.description
    session.commit()
    return _project_out(session, project)


@router.delete("/projects/{project_id}")
def delete_project(project_id: int, session: Session = Depends(get_db)) -> dict:
    """Delete a project. Its videos and clips are kept and simply become unassigned."""
    project = session.get(Project, project_id)
    if project is None:
        raise NotFound("That project does not exist")
    session.delete(project)
    session.commit()
    return {"deleted": True}


# ----------------------------------------------------------------------------------------- your presets
MAX_PRESETS = 50


@router.get("/presets", response_model=list[PresetOut])
def list_presets(session: Session = Depends(get_db)) -> list[Preset]:
    """Your own presets for the AI Clipper's options, by name."""
    return sorted(session.scalars(select(Preset)), key=lambda preset: preset.name.lower())


@router.post("/presets", response_model=PresetOut)
def save_preset(body: PresetIn, session: Session = Depends(get_db)) -> Preset:
    """Save the clipping options under a name. They are checked by the same rules as an analysis, so the preset
    always works when it is used. Saving again under the same name (whatever its capitals) updates it."""
    name = " ".join(body.name.split())
    if not name:
        raise ClipForgeError("Give the preset a name.")
    try:
        options = AnalyzeIn.model_validate(body.config)
    except ValidationError as exc:
        problem = exc.errors()[0]
        where = ".".join(str(part) for part in problem.get("loc", ())) or "options"
        raise ClipForgeError(f"This preset would not work ({where}: {problem.get('msg', 'not valid')}).") from None
    if options.game_style and options.game_style not in GAME_STYLES:
        raise ClipForgeError(f"There is no game called “{options.game_style}” in ClipForge's list.")
    config = options.model_dump(include=set(SAVED_OPTIONS))
    existing = next((p for p in session.scalars(select(Preset)) if p.name.lower() == name.lower()), None)
    if existing is None:
        if (session.scalar(select(func.count(Preset.id))) or 0) >= MAX_PRESETS:
            raise ClipForgeError(f"You can keep up to {MAX_PRESETS} presets. Remove one you no longer use first.")
        existing = Preset(name=name, config=config)
        session.add(existing)
    else:
        existing.name, existing.config = name, config
    session.commit()
    session.refresh(existing)
    log.info("preset saved", extra={"preset_id": existing.id})
    return existing


@router.delete("/presets/{preset_id}")
def delete_preset(preset_id: int, session: Session = Depends(get_db)) -> dict:
    preset = session.get(Preset, preset_id)
    if preset is None:
        raise NotFound("That preset no longer exists.")
    session.delete(preset)
    session.commit()
    return {"deleted": True}
