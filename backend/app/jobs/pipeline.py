"""The two background jobs: analyse a video (transcribe, measure, find moments) and render a clip.

Every expensive step writes its result into the video's work folder (audio, transcript, signals), and
the next run reuses whatever is already there. That is what makes re-analysis with different settings
fast, and what lets an interrupted job continue instead of starting over.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import db
from ..analysis.audio import AudioFeatures, compute_audio_features
from ..analysis.dedupe import ExistingClip
from ..analysis import chatgpt_review, claude_review
from ..analysis.describe import cover_words, credit_line, post_caption
from ..analysis.engine import (HighlightOptions, apply_second_opinion, find_highlights, highlights_from_picks,
                               moment_highlight)
from ..analysis.feedback import FeedbackProfile
from ..analysis.lexicon import content_tokens
from ..analysis.presets import CONTENT_PRESETS, LENGTH_RANGES
from ..analysis.visual import VisualFeatures, compute_visual_features
from ..config import get_settings
from ..editing.captions import (CaptionWord, build_ass, build_srt, build_vtt, mark_emphasis, resolve_style,
                                words_for_clip)
from ..editing.qc import run_checks, summarize
from ..editing.reframe import ASPECTS, LAYOUTS, FaceSummary, analyze_faces, plan_layout
from ..editing.render import RenderRequest, render_clip
from ..editing.thumbnail import can_draw, make_cover
from ..errors import ClipForgeError, DependencyMissing, JobCancelled, NotFound
from ..models import Clip, Feedback, Job, Notification, Project, TranscriptSegment, Video
from ..security.paths import safe_filename
from ..services import ffmpeg, hardware, settings_store, storage
from ..services.library import register_video
from ..transcription.align import refine_word_times, speech_envelope, timing_report
from ..transcription.sentences import Word, build_sentences
from ..transcription.whisper_engine import TranscribeOptions, transcribe_wav
from . import queue

log = logging.getLogger("clipforge.pipeline")

# Set when the worker is asked to stop. The running job stops at its next check and goes back to the
# queue, so it continues (from its saved intermediate results) the next time the app starts.
SHUTDOWN = threading.Event()

# (key, label shown in the app, share of the job's time)
STAGES: dict[str, list[tuple[str, str, int]]] = {
    "analyze": [("audio", "Extracting audio", 3), ("transcribe", "Transcribing speech", 60),
                ("signals", "Measuring sound and picture", 14), ("highlights", "Finding the best moments", 8),
                ("review", "Claude is picking the clips", 12),
                ("second", "ChatGPT is checking the picks", 10),
                ("save", "Saving suggestions", 2)],
    "render": [("framing", "Framing the shot", 8), ("captions", "Building captions", 3),
               ("encode", "Rendering video", 75), ("checks", "Checking the export", 10),
               ("cover", "Making the thumbnail", 4)],
    "import": [("inspect", "Checking the link", 4), ("download", "Downloading the video", 88),
               ("register", "Adding it to your library", 8)],
    "moment": [("audio", "Extracting audio", 3), ("transcribe", "Transcribing speech", 62),
               ("review", "Claude is finishing your clip", 20), ("second", "ChatGPT is checking it", 12),
               ("save", "Saving your clip", 3)],
    "publish": [("prepare", "Checking the post", 5), ("upload", "Uploading", 80),
                ("confirm", "Waiting for the platform to confirm", 15)],
}


def stage_list(kind: str) -> list[dict[str, Any]]:
    return [{"key": key, "label": label, "status": "pending", "progress": 0.0, "note": ""}
            for key, label, _ in STAGES[kind]]


class JobContext:
    """Progress reporting and cancellation for one running job."""

    def __init__(self, job_id: int, kind: str) -> None:
        self.job_id = job_id
        self.stages = stage_list(kind)
        self.weights = {key: weight for key, _, weight in STAGES[kind]}
        self._current: dict[str, Any] | None = None
        self._last_write = 0.0
        self._last_poll = 0.0
        self._cancel = False
        self.message = ""
        self.worklog: list[dict[str, str]] = []   # short lines: who did what ("forge", "claude", "chatgpt")

    def say(self, actor: str, text: str) -> None:
        """Add one short line to the job's work log."""
        self.worklog.append({"actor": actor, "text": text})

    def cancelled(self, force: bool = False) -> bool:
        if SHUTDOWN.is_set():
            return True
        now = time.monotonic()
        if not self._cancel and (force or now - self._last_poll >= 0.4):
            self._last_poll = now
            with db.session_scope() as s:
                self._cancel = queue.is_cancel_requested(s, self.job_id)
        return self._cancel

    def check(self) -> None:
        if self.cancelled(force=True):
            raise JobCancelled()

    def _overall(self) -> float:
        total = sum(self.weights.values())
        done = sum(self.weights[s["key"]] * (1.0 if s["status"] in ("done", "skipped") else s["progress"])
                   for s in self.stages)
        return done / total

    def _write(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_write < 0.3:
            return
        self._last_write = now
        with db.session_scope() as s:
            queue.update_progress(s, self.job_id, progress=self._overall(), message=self.message,
                                  stage=self._current["key"] if self._current else "", stages=self.stages)

    def _close_current(self) -> None:
        if self._current is not None and self._current["status"] == "running":
            self._current["status"] = "done"
            self._current["progress"] = 1.0

    def start(self, key: str, message: str = "") -> None:
        self.check()
        self._close_current()
        self._current = next(s for s in self.stages if s["key"] == key)
        self._current["status"] = "running"
        self.message = message or self._current["label"]
        self._write(force=True)

    def skip(self, key: str, note: str) -> None:
        self._close_current()
        stage = next(s for s in self.stages if s["key"] == key)
        stage.update(status="skipped", progress=1.0, note=note)
        self._current = None
        self._write(force=True)

    def progress(self, fraction: float, message: str | None = None) -> None:
        if self._current is None:
            return
        self._current["progress"] = round(min(1.0, max(0.0, fraction)), 3)
        if message:
            self.message = message
        self._write()

    def note(self, text: str) -> None:
        if self._current is not None:
            self._current["note"] = text

    def finish(self) -> None:
        self._close_current()
        self._current = None
        self._write(force=True)


# ---------------------------------------------------------------------------------------------- options
@dataclass
class AnalyzeOptions:
    count: int = 5
    min_len: float = 15.0
    max_len: float = 60.0
    min_score: float = 0.0
    content_preset: str = "auto"
    game_style: str = ""
    aspect: str = "9:16"
    layout: str = "auto"
    captions: str = "opus"
    quality: str = "balanced"
    auto_render: bool = True
    language: str | None = None
    remove_silence: bool = False
    show_hook: bool = True

    @classmethod
    def from_payload(cls, raw: dict[str, Any] | None) -> AnalyzeOptions:
        raw = raw or {}
        opts = cls()
        low, high = LENGTH_RANGES.get(str(raw.get("length", "")), (opts.min_len, opts.max_len))
        opts.min_len = float(raw.get("min_len", low))
        opts.max_len = float(raw.get("max_len", high))
        opts.min_len = min(max(3.0, opts.min_len), 300.0)
        opts.max_len = min(max(opts.min_len + 1.0, opts.max_len), 600.0)
        opts.count = int(min(30, max(1, raw.get("count", opts.count))))
        opts.min_score = float(min(100, max(0, raw.get("min_score", 0))))
        if raw.get("content_preset") in CONTENT_PRESETS:
            opts.content_preset = raw["content_preset"]
        opts.game_style = str(raw.get("game_style", ""))[:20]
        if raw.get("aspect") in ASPECTS:
            opts.aspect = raw["aspect"]
        if raw.get("layout") in LAYOUTS:
            opts.layout = raw["layout"]
        opts.captions = str(raw.get("captions", opts.captions))[:20]
        if raw.get("quality") in hardware.QUALITIES:
            opts.quality = raw["quality"]
        opts.auto_render = bool(raw.get("auto_render", True))
        language = str(raw.get("language") or "").strip().lower()
        opts.language = None if language in ("", "auto") else language[:8]
        opts.remove_silence = bool(raw.get("remove_silence", False))
        opts.show_hook = bool(raw.get("show_hook", True))
        return opts

    def clip_settings(self) -> dict[str, Any]:
        """The edit decisions a new suggestion starts with. The editor changes this same structure."""
        return {"aspect": self.aspect, "layout": self.layout, "subject_x": None, "zoom": 1.0, "facecam_box": None,
                "gameplay_box": None, "captions": {"enabled": True, "preset": self.captions},
                "hook": {"enabled": self.show_hook, "seconds": 4.0},
                "audio": {"normalize": True, "denoise": False}, "remove_silence": self.remove_silence,
                "quality": self.quality, "fps": 30, "preset": self.content_preset, "game_style": self.game_style}


# ----------------------------------------------------------------------------------------------- helpers
def enqueue_analysis(session: Session, video: Video, options: dict[str, Any]) -> Job:
    return queue.enqueue(session, "analyze", {"options": options}, video_id=video.id, stages=stage_list("analyze"),
                         target=("video", video.id))


def enqueue_render(session: Session, clip: Clip, priority: int = 5) -> Job:
    clip.render_status = "queued"
    clip.render_error = ""
    return queue.enqueue(session, "render", {}, video_id=clip.video_id, clip_id=clip.id, priority=priority,
                         stages=stage_list("render"), target=("clip", clip.id))


def enqueue_import(session: Session, url: str, *, permission_status: str, permission_note: str = "",
                   creator_name: str = "", title: str | None = None, project_id: int | None = None,
                   max_height: int = 1080, analyze: dict[str, Any] | None = None) -> Job:
    """Queue the import of one video from a link. The link must already have been checked by
    downloader.validate_source_url. The folder it will be saved in is chosen now, so a download that is
    interrupted by closing the app continues in the same place."""
    payload = {"url": url, "permission_status": permission_status, "permission_note": permission_note,
               "creator_name": creator_name, "title": title, "project_id": project_id, "max_height": max_height,
               "analyze": analyze, "storage_key": storage.new_key()}
    return queue.enqueue(session, "import", payload, stages=stage_list("import"), target=("url", url))


def notify(session: Session, kind: str, title: str, body: str = "", link: str = "") -> None:
    session.add(Notification(kind=kind, title=title[:200], body=body, link=link))


def load_transcriber(session: Session):  # noqa: ANN201
    """(the speech model, a signature naming model and device, the device). Loads the model."""
    from ..transcription import models
    from ..transcription.whisper_engine import get_transcriber

    cfg = settings_store.get_all(session)
    hw = hardware.detect()
    recommended = hardware.recommend(hw, cfg["processing.priority"], models.installed_models())
    name = cfg["processing.whisper_model"]
    if name == "auto" or name not in models.WHISPER_MODELS or not models.is_installed(name):
        name = recommended["whisper_model"]
    if not name:
        raise DependencyMissing("No speech model is installed",
                                hint="Install one in Settings → Models, or run scripts\\setup.ps1.")
    device, compute = recommended["whisper_device"], recommended["whisper_compute"]
    if cfg["processing.whisper_device"] == "cpu" or not hw.get("whisper_cuda", {}).get("ok"):
        device, compute = "cpu", "int8"
    if device == "cuda":
        hardware.register_cuda_dlls()
    return get_transcriber(models.model_dir(name), device, compute), f"{name}|{device}|{compute}", device


def resolve_encoder(session: Session) -> str:
    wanted = settings_store.get(session, "processing.video_encoder")
    nvenc_ok = bool(hardware.detect().get("encoders", {}).get("h264_nvenc", {}).get("ok"))
    if wanted == "libx264" or not nvenc_ok:
        return "libx264"
    return "h264_nvenc"


def load_words(session: Session, video_id: int, start: float | None = None, end: float | None = None) -> list[Word]:
    query = select(TranscriptSegment).where(TranscriptSegment.video_id == video_id).order_by(TranscriptSegment.idx)
    if start is not None:
        query = query.where(TranscriptSegment.end >= start)
    if end is not None:
        query = query.where(TranscriptSegment.start <= end)
    words: list[Word] = []
    for segment in session.scalars(query):
        words.extend(Word(str(w[0]), float(w[1]), float(w[2]), float(w[3]) if len(w) > 3 else 1.0)
                     for w in segment.words or [])
    return words


def store_transcript(session: Session, video_id: int, words: list[Word]) -> int:
    """Replace the video's transcript rows with sentences built from these words."""
    session.query(TranscriptSegment).filter(TranscriptSegment.video_id == video_id).delete()
    sentences = build_sentences(words)
    for sentence in sentences:
        session.add(TranscriptSegment(
            video_id=video_id, idx=sentence.idx, start=sentence.start, end=sentence.end, text=sentence.text,
            words=[[w.text, w.start, w.end, round(w.prob, 3)] for w in words[sentence.w0:sentence.w1]
                   if w.text.strip()]))
    return len(sentences)


def clip_segments(start: float, end: float, words: list[Word], remove_silence: bool, min_gap: float = 0.8,
                  keep: float = 0.12, cuts: Sequence[Sequence[float]] | None = None) -> list[tuple[float, float]]:
    """The source ranges a clip is made of. With silence removal, pauses longer than min_gap are cut down
    to a short breath (keep on each side), so the clip tightens without words colliding. Parts you left out
    (cuts: [from, to] in seconds of the source video) are taken out last."""
    if not remove_silence:
        return leave_out([(round(start, 3), round(end, 3))], cuts)
    inside = [w for w in words if w.end > start and w.start < end]
    segments: list[tuple[float, float]] = []
    cursor = start
    for a, b in zip(inside, inside[1:]):
        if b.start - a.end >= min_gap:
            cut_from, cut_to = a.end + keep, b.start - keep
            if cut_to - cut_from >= 0.15 and cut_from > cursor:
                segments.append((cursor, cut_from))
                cursor = cut_to
    segments.append((cursor, end))
    return leave_out([(round(a, 3), round(b, 3)) for a, b in segments if b - a >= 0.1], cuts)


def leave_out(segments: list[tuple[float, float]], cuts: Sequence[Sequence[float]] | None
              ) -> list[tuple[float, float]]:
    """The segments without the parts left out. Parts that would leave nothing are ignored: a clip is never
    rendered empty."""
    if not cuts:
        return segments
    pieces = list(segments)
    for c, d in sorted((float(c), float(d)) for c, d in cuts):
        kept: list[tuple[float, float]] = []
        for a, b in pieces:
            if d <= a or c >= b:
                kept.append((a, b))
                continue
            if c > a:
                kept.append((a, c))
            if d < b:
                kept.append((d, b))
        pieces = kept
    pieces = [(round(a, 3), round(b, 3)) for a, b in pieces if b - a >= 0.1]
    return pieces or segments


def cut_into_word(start: float, end: float, words: list[Word]) -> tuple[str, str] | None:
    for w in words:
        if w.start + 0.06 < start < w.end - 0.06:
            return "start", w.text
        if w.start + 0.06 < end < w.end - 0.06:
            return "end", w.text
    return None


def _clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def build_post_kit(clip: Clip, video: Video) -> str:
    """The text that goes with a clip when it is posted, plus where it came from."""
    link = video.source_url
    if link and ("youtube.com" in link or "youtu.be" in link):
        link += ("&" if "?" in link else "?") + f"t={int(clip.start)}s"
    permission = {"owned": "Your own content", "licensed": "Licensed (campaign or agreement)",
                  "permission": "Creator gave permission", "denied": "NOT PERMITTED",
                  "unknown": "Not recorded: check before posting"}.get(video.permission_status, video.permission_status)
    credit = credit_line(video.attribution, video.creator_name)
    tags = " ".join(clip.hashtags or [])
    words = clip.cover_text or cover_words(clip.title)[0]
    thumbnail = (["THUMBNAIL", f"The .jpg next to this file, with the words “{words}” on it. Use it as the cover "
                               "where the app lets you upload one.", ""] if clip.cover_path else [])
    lines = [clip.title, "=" * max(8, len(clip.title)), "", "TITLE", clip.title, "", "ON-SCREEN HOOK", clip.hook_text,
             "", "DESCRIPTION", clip.description or clip.title, "", "HASHTAGS", tags, "",
             "TIKTOK CAPTION (copy and paste)",
             clip.tiktok_caption or post_caption(clip.description or clip.title, credit, clip.hashtags or []),
             "", *thumbnail,
             "SOURCE", f"Video: {video.title}", f"Creator: {video.creator_name or 'not recorded'}",
             f"Link: {link or 'local file'}", f"Time in source: {_clock(clip.start)} to {_clock(clip.end)}",
             f"Rights: {permission}" + (f" ({video.permission_note})" if video.permission_note else ""),
             "Credit does not replace permission.", "",
             "ESTIMATE", f"Engagement potential: {clip.score:.0f}/100 (heuristic estimate, not a prediction)",
             f"Why: {clip.explanation}", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------- analyse
def _transcript(ctx: JobContext, video: Video, source: Path, work: Path, language_option: str | None,
                timing: dict[str, float]) -> tuple[list[Word], str, str, str]:
    """Extract the audio and transcribe it, or reuse what an earlier job saved. Shared by the analysis and by
    clips made from your own timestamps. Returns (words, language, transcriber signature, note)."""
    note = ""
    words: list[Word] = []
    language = ""
    signature = ""
    if video.has_audio:
        wav = work / "audio.wav"
        marker = work / "audio.ok"
        if wav.exists() and marker.exists():
            ctx.skip("audio", "Reused the extracted audio")
        else:
            ctx.start("audio")
            started = time.monotonic()
            ffmpeg.extract_audio(source, wav, duration=video.duration, on_progress=ctx.progress,
                                 should_cancel=ctx.cancelled)
            marker.write_text(str(wav.stat().st_size), encoding="utf-8")
            timing["audio"] = round(time.monotonic() - started, 1)

        transcript_file = work / "transcript.json"
        saved = None
        if transcript_file.exists():
            try:
                saved = json.loads(transcript_file.read_text(encoding="utf-8"))
            except ValueError:
                saved = None
        wanted_language = language_option or "auto"
        try:
            with db.session_scope() as s:
                transcriber, model_signature, device = load_transcriber(s)
            signature = f"{model_signature}|{wanted_language}"
        except DependencyMissing:
            if saved is None:
                raise
            transcriber, signature = None, saved.get("signature", "")  # no model, but a transcript exists

        if saved is not None and saved.get("signature") == signature:
            ctx.skip("transcribe", "Reused the saved transcript")
            ctx.say("forge", "Reused the transcript it had already made.")
            language = saved.get("language", "")
            with db.session_scope() as s:
                words = load_words(s, video.id)
                if not words:  # rows were lost (for example the database was reset): rebuild from the file
                    words = [Word(t, a, b, p) for t, a, b, p in saved["words"]]
                    store_transcript(s, video.id, words)
        else:
            ctx.start("transcribe")
            started = time.monotonic()
            result = transcribe_wav(wav, transcriber, TranscribeOptions(language=language_option),
                                    checkpoint_path=work / "transcript.partial.json", signature=signature,
                                    on_progress=lambda f: ctx.progress(f, f"Transcribing speech ({f:.0%})"),
                                    should_cancel=ctx.cancelled)
            words = refine_word_times(result.words, speech_envelope(wav))
            language = result.language
            timing["transcribe"] = round(time.monotonic() - started, 1)
            ctx.say("forge", f"Transcribed {_clock(video.duration)} of speech ({len(words):,} words) on the "
                             f"{'graphics card' if '|cuda|' in signature else 'processor'} in "
                             f"{timing['transcribe']:.0f} s.")
            ctx.note(f"{len(words)} words" + (f", resumed after {result.resumed_windows} finished parts"
                                              if result.resumed_windows else ""))
            log.info("transcribed", extra={"video_id": video.id, "words": len(words), "seconds": timing["transcribe"],
                                           "model": signature, **timing_report(result.words, words)})
            tmp = transcript_file.with_suffix(".tmp")
            tmp.write_text(json.dumps({"signature": signature, "language": language,
                                       "words": [[w.text, w.start, w.end, w.prob] for w in words]}), encoding="utf-8")
            os.replace(tmp, transcript_file)
            with db.session_scope() as s:
                store_transcript(s, video.id, words)
    else:
        ctx.skip("audio", "This video has no audio track")
        ctx.skip("transcribe", "No audio to transcribe")
        note = "This video has no audio track, so it was analysed from the picture alone."
    return words, language, signature, note


def _analyze(ctx: JobContext, job: dict[str, Any]) -> dict[str, Any]:
    opts = AnalyzeOptions.from_payload(job["payload"].get("options"))
    with db.session_scope() as s:
        video = s.get(Video, job["video_id"])
        if video is None:
            raise NotFound("This video no longer exists")
        job["previous_status"] = video.status
        video.status, video.last_error = "analyzing", ""
        s.flush()
        s.expunge(video)
    source = storage.video_source_path(video)
    work = storage.video_work_dir(video)
    timing: dict[str, float] = {}

    # ---- audio + transcript ---------------------------------------------------------------------
    words, language, signature, note = _transcript(ctx, video, source, work, opts.language, timing)

    # ---- signals ---------------------------------------------------------------------------------
    ctx.start("signals")
    started = time.monotonic()
    audio: AudioFeatures | None = None
    if video.has_audio:
        audio_file = work / "audio.npz"
        if audio_file.exists():
            audio = AudioFeatures.load(audio_file)
        else:
            audio = compute_audio_features(work / "audio.wav", should_cancel=ctx.cancelled,
                                           on_progress=lambda f: ctx.progress(f * 0.2))
            audio.save(audio_file)
    visual_file = work / "visual.npz"
    if visual_file.exists():
        visual = VisualFeatures.load(visual_file)
    else:
        visual = compute_visual_features(source, video.duration, src_size=(video.width, video.height),
                                         on_progress=lambda f: ctx.progress(0.2 + f * 0.8),
                                         should_cancel=ctx.cancelled)
        visual.save(visual_file)
    timing["signals"] = round(time.monotonic() - started, 1)

    # ---- highlights ------------------------------------------------------------------------------
    ctx.start("highlights")
    started = time.monotonic()
    sentences = build_sentences(words)
    with db.session_scope() as s:
        cfg = settings_store.get_all(s)
        kept, replaced = [], []
        for clip in s.scalars(select(Clip).where(Clip.video_id == video.id)):
            untouched = (clip.origin == "ai" and clip.review_status == "pending" and not clip.favorite
                         and clip.captions is None and clip.manual_rank is None)
            (replaced if untouched else kept).append(clip)
        existing = [ExistingClip(c.start, c.end, frozenset(content_tokens(c.transcript_text))) for c in kept]
        replaced_ids = [c.id for c in replaced]
        feedback = FeedbackProfile.from_labels(
            (f.label, (f.snapshot or {}).get("category", "")) for f in s.scalars(select(Feedback)))
    highlights = find_highlights(
        words, sentences, audio, visual,
        HighlightOptions(count=opts.count, min_len=opts.min_len, max_len=opts.max_len, min_score=opts.min_score,
                         preset=opts.content_preset, game_style=opts.game_style,
                         niche_keywords=tuple(cfg["niche.keywords"]), language=language or "en"),
        duration=video.duration, existing=existing, feedback=feedback, video_title=video.title,
        creator=video.creator_name, should_cancel=ctx.cancelled, on_progress=ctx.progress)
    timing["highlights"] = round(time.monotonic() - started, 1)

    def titles(items: Sequence[Any], limit: int = 3) -> str:
        names = [h if isinstance(h, str) else h.title for h in items]
        shown = ", ".join(f"“{name}”" for name in names[:limit])
        return shown + (f" and {len(names) - limit} more" if len(names) > limit else "")

    def count(n: int, word: str) -> str:
        return f"{n} {word}{'' if n == 1 else 's'}"

    if cfg["ai.engine"] == "claude" and sentences:
        ctx.say("forge", f"Measured sound and picture and made {len(highlights)} backup picks with its own rules.")
    elif highlights:
        ctx.say("forge", f"Picked {count(len(highlights), 'clip')} with its own rules: {titles(highlights)}.")

    # ---- Claude picks the clips (the local picks above are the fallback) ----------------------------
    if cfg["ai.engine"] != "claude":
        ctx.skip("review", "Local rules only (Settings)")
    elif not sentences:
        ctx.skip("review", "Nothing was said, so the local rules picked from sound and picture")
    else:
        ctx.start("review")
        started = time.monotonic()
        hl_options = HighlightOptions(count=opts.count, min_len=opts.min_len, max_len=opts.max_len,
                                      min_score=opts.min_score, preset=opts.content_preset,
                                      game_style=opts.game_style, niche_keywords=tuple(cfg["niche.keywords"]),
                                      language=language or "en")
        action_times = sorted({round(h.peak_time, 1) for h in highlights if h.analysis_source == "signals-only"}
                              | {round(float(e["start"]), 1) for e in (audio.loud_events if audio else [])})
        prompt = claude_review.build_prompt(sentences, hl_options, title=video.title, creator=video.creator_name,
                                            game=opts.game_style, action_times=action_times,
                                            duration=video.duration)
        try:
            picks = claude_review.ask_claude(prompt, model=str(cfg["ai.claude_model"]), should_cancel=ctx.cancelled)
            chosen = highlights_from_picks(words, sentences, audio, visual, hl_options, picks,
                                           duration=video.duration, existing=existing, feedback=feedback,
                                           video_title=video.title, creator=video.creator_name)
            if chosen:
                highlights = chosen
                ctx.note(f"Claude ({cfg['ai.claude_model']}) picked {len(chosen)}")
                ctx.say("claude", f"Picked {count(len(chosen), 'clip')}: {titles(chosen)}.")
            else:
                ctx.note("Claude found nothing above your minimum score; showing the local picks")
                ctx.say("claude", "Found nothing above your minimum score, so Forge's own picks were kept.")
        except claude_review.ClaudeUnavailable as exc:
            log.warning("Claude review unavailable: %s", exc.message)
            ctx.note("Not used: " + exc.message.split(",")[0])
            ctx.say("claude", f"Could not help: {exc.message.split(',')[0].rstrip('.')}. Forge's own picks were used.")
            note = exc.message + (" " + exc.hint if exc.hint else "")
        timing["review"] = round(time.monotonic() - started, 1)

    # ---- ChatGPT checks the picks (second opinion) ---------------------------------------------------
    if cfg["ai.engine"] != "claude":
        ctx.skip("second", "Local rules only: nothing leaves this PC")
    elif cfg["ai.second_opinion"] != "chatgpt":
        ctx.skip("second", "Off in Settings")
    elif not highlights or not sentences:
        ctx.skip("second", "Nothing to check")
    else:
        ctx.start("second")
        started = time.monotonic()
        second_options = HighlightOptions(count=opts.count, min_len=opts.min_len, max_len=opts.max_len,
                                          min_score=opts.min_score, preset=opts.content_preset,
                                          game_style=opts.game_style, niche_keywords=tuple(cfg["niche.keywords"]),
                                          language=language or "en")
        prompt = chatgpt_review.build_prompt(sentences, highlights, second_options, title=video.title,
                                             creator=video.creator_name)
        try:
            review = chatgpt_review.ask_chatgpt(prompt, model=str(cfg["ai.chatgpt_model"]),
                                                should_cancel=ctx.cancelled)
            highlights, summary = apply_second_opinion(
                highlights, review, words, sentences, audio, visual, second_options, duration=video.duration,
                existing=existing, feedback=feedback, video_title=video.title, creator=video.creator_name)
            agreed, disagreed, added = summary["agreed"], summary["disagreed"], summary["added"]
            ctx.note(f"Agreed with {len(agreed)}, disagreed with {len(disagreed)}, added {len(added)}")
            line = f"Checked {count(len(agreed) + len(disagreed), 'pick')}: agreed with {len(agreed)}"
            if disagreed:   # the reasons are on the clips themselves; the log stays short
                line += f", disagreed with {len(disagreed)} ({titles([t for t, _ in disagreed], 2)})"
            if added:
                line += f", added {len(added)} ({titles(added, 2)})"
            ctx.say("chatgpt", line + ".")
        except chatgpt_review.ChatGPTUnavailable as exc:
            log.warning("ChatGPT second opinion unavailable: %s", exc.message)
            ctx.note("Not used: " + exc.message.split(",")[0])
            ctx.say("chatgpt", f"Could not check the picks: {exc.message.split(',')[0].rstrip('.')}.")
            note = (note + " " if note else "") + exc.message + (" " + exc.hint if exc.hint else "")
        except JobCancelled:
            raise
        except Exception:  # noqa: BLE001  (an optional second opinion must never cost the user the picks)
            log.warning("ChatGPT second opinion failed", exc_info=True)
            ctx.note("Not used: unexpected error")
            ctx.say("chatgpt", "Could not check the picks: something unexpected went wrong.")
        timing["second"] = round(time.monotonic() - started, 1)

    # ---- save ------------------------------------------------------------------------------------
    ctx.start("save")
    settings = opts.clip_settings()
    settings["captions"]["censor"] = bool(cfg["captions.censor"])
    with db.session_scope() as s:
        for clip in s.scalars(select(Clip).where(Clip.id.in_(replaced_ids))) if replaced_ids else []:
            storage.remove_tree(get_clip_dir(clip))
            s.delete(clip)
        s.flush()
        new_clips = []
        for h in highlights:
            clip = Clip(video_id=video.id, project_id=video.project_id, storage_key=storage.new_key(), origin="ai",
                        title=h.title, hook_text=h.hook_text, hook_options=h.hook_options, description=h.description,
                        hashtags=h.hashtags, category=h.category, transcript_text=h.transcript_text, start=h.start,
                        end=h.end, score=h.score, subscores=h.subscores, confidence=h.confidence,
                        explanation=h.explanation, evidence=h.evidence, analysis_source=h.analysis_source,
                        rank=h.rank, settings=settings, cover_text=h.extra.get("cover_text", ""),
                        cover_word=h.extra.get("cover_word", ""))
            s.add(clip)
            new_clips.append(clip)
        s.flush()
        if opts.auto_render:
            for clip in new_clips:
                enqueue_render(s, clip)
        stored = s.get(Video, video.id)
        stored.status = "analyzed"
        stored.language = language
        stored.content_preset = opts.content_preset
        stored.analysis = {
            "words": len(words), "sentences": len(sentences), "transcriber": signature, "timing": timing,
            "loud_events": len(audio.loud_events) if audio else 0, "scene_cuts": len(visual.cuts),
            "suggestions": len(highlights), "note": note,
            "options": {"count": opts.count, "min_len": opts.min_len, "max_len": opts.max_len,
                        "min_score": opts.min_score, "content_preset": opts.content_preset}}
        if highlights:
            body = "Rendering has started." if opts.auto_render else "Open the AI Clipper to review them."
            notify(s, "clips_ready", f"{len(highlights)} moments found in “{video.title}”", body, f"/clipper?video={video.id}")
        else:
            notify(s, "info", f"No moments above your minimum score in “{video.title}”",
                   "Lower the minimum score or widen the clip length and analyse again.", f"/clipper?video={video.id}")
    if not highlights and not note:
        note = "No moment scored above the minimum. Try a lower minimum score or a wider length range."
    if highlights:
        ctx.say("forge", f"Saved {len(highlights)} clip{'s' if len(highlights) != 1 else ''}"
                         + (" and started rendering them." if opts.auto_render else " for you to review."))
    else:
        ctx.say("forge", "Saved no clips: nothing scored above your minimum.")
    return {"clips": len(highlights), "words": len(words), "note": note, "timing": timing}


# --------------------------------------------------------------------------------------- your own moments
def enqueue_moment(session: Session, video: Video, *, start: float, end: float | None,
                   options: dict[str, Any] | None = None) -> Job:
    """Queue a clip from a moment you chose. Asking again for the same start while it waits returns that job."""
    payload = {"start": round(float(start), 3), "end": None if end is None else round(float(end), 3),
               "options": dict(options or {})}
    return queue.enqueue(session, "moment", payload, video_id=video.id, priority=8, stages=stage_list("moment"),
                         target=("moment", f"{video.id}@{payload['start']:.1f}"))


def _moment(ctx: JobContext, job: dict[str, Any]) -> dict[str, Any]:
    """The same work as for a moment the AI found, for one you chose: transcript, end point, title, hook and score
    (Claude, or the local rules), ChatGPT's verdict, then the render. Your start is never moved."""
    payload = job["payload"]
    opts = AnalyzeOptions.from_payload(payload.get("options"))
    start = float(payload["start"])
    wanted_end = None if payload.get("end") is None else float(payload["end"])
    with db.session_scope() as s:
        video = s.get(Video, job["video_id"])
        if video is None:
            raise NotFound("This video no longer exists")
        s.expunge(video)
        cfg = settings_store.get_all(s)
        feedback = FeedbackProfile.from_labels(
            (f.label, (f.snapshot or {}).get("category", "")) for f in s.scalars(select(Feedback)))
    if start >= video.duration:
        raise ClipForgeError(f"Your moment starts at {_clock(start)}, but the video is only {_clock(video.duration)} long.")
    source = storage.video_source_path(video)
    work = storage.video_work_dir(video)
    timing: dict[str, float] = {}
    words, language, _signature, note = _transcript(ctx, video, source, work, opts.language, timing)
    sentences = build_sentences(words)
    audio = AudioFeatures.load(work / "audio.npz") if (work / "audio.npz").exists() else None
    visual = VisualFeatures.load(work / "visual.npz") if (work / "visual.npz").exists() else None
    options = HighlightOptions(count=1, min_len=opts.min_len, max_len=opts.max_len, min_score=0.0,
                               preset=opts.content_preset, game_style=opts.game_style,
                               niche_keywords=tuple(cfg["niche.keywords"]), language=language or "en")
    reach = wanted_end if wanted_end is not None else start + opts.max_len
    spoken = any(s.end > start and s.start < reach for s in sentences)

    # ---- Claude finds the end (when you gave none) and names it ----------------------------------------
    pick = None
    if cfg["ai.engine"] != "claude":
        ctx.skip("review", "Local rules only (Settings)")
    elif not spoken:
        ctx.skip("review", "Nothing is said in your moment, so the local rules finished it")
    else:
        ctx.start("review")
        started = time.monotonic()
        prompt = claude_review.build_moment_prompt(sentences, options, start=start, end=wanted_end, title=video.title,
                                                   creator=video.creator_name, game=opts.game_style,
                                                   duration=video.duration)
        try:
            pick = claude_review.ask_claude(prompt, model=str(cfg["ai.claude_model"]), should_cancel=ctx.cancelled)[0]
            ctx.note(f"Claude ({cfg['ai.claude_model']}) finished it")
        except claude_review.ClaudeUnavailable as exc:
            reason = exc.message.split(",")[0].rstrip(".")
            log.warning("Claude unavailable for a moment: %s", exc.message)
            ctx.note("Not used: " + reason)
            ctx.say("claude", f"Could not help: {reason}. Forge named your moment instead.")
            note = f"{reason}, so Forge named your moment." + (f" {exc.hint}" if exc.hint else "")
        timing["review"] = round(time.monotonic() - started, 1)

    highlight = moment_highlight(words, sentences, audio, visual, options, start=start, end=wanted_end, pick=pick,
                                 duration=video.duration, feedback=feedback, video_title=video.title,
                                 creator=video.creator_name)
    end_from = highlight.extra.get("end_from")
    for side, shift in (highlight.extra.get("moved") or {}).items():
        word = shift["word"].strip(".,!?;:")
        ctx.say("forge", f"Moved your {side} {shift['by']:.1f} s later so it does not cut into “{word}”.")
    if pick is not None:
        if end_from == "claude":
            ctx.say("claude", f"Ended your moment at {_clock(highlight.end)}, right after the payoff, and named it "
                              f"“{highlight.title}”.")
        else:
            ctx.say("claude", f"Named your moment “{highlight.title}” and scored it {highlight.score:.0f}.")
    if end_from == "forge":
        why = " because Claude's end did not fit your length" if pick is not None else ""
        ctx.say("forge", f"Ended your moment at {_clock(highlight.end)}, at the end of a sentence{why}.")

    # ---- ChatGPT's verdict (nothing is added: this is your moment) -------------------------------------
    if cfg["ai.engine"] != "claude":
        ctx.skip("second", "Local rules only: nothing leaves this PC")
    elif cfg["ai.second_opinion"] != "chatgpt":
        ctx.skip("second", "Off in Settings")
    elif not highlight.transcript_text:
        ctx.skip("second", "Nothing is said in your moment")
    else:
        ctx.start("second")
        started = time.monotonic()
        prompt = chatgpt_review.build_prompt(sentences, [highlight], options, title=video.title,
                                             creator=video.creator_name, chosen_by_user=True)
        try:
            review = chatgpt_review.ask_chatgpt(prompt, model=str(cfg["ai.chatgpt_model"]),
                                                should_cancel=ctx.cancelled)
            review.additions = []
            (highlight,), _summary = apply_second_opinion(
                [highlight], review, words, sentences, audio, visual, options, duration=video.duration,
                feedback=feedback, video_title=video.title, creator=video.creator_name)
            verdict = review.verdicts.get(1)
            if verdict:
                kind, why = verdict
                line = f"Checked your moment and {'agreed' if kind == 'agree' else 'disagreed'}" + (f": {why}" if why else "")
                ctx.say("chatgpt", line if line.endswith((".", "!", "?")) else line + ".")
            else:
                ctx.say("chatgpt", "Looked at your moment but gave no verdict.")
        except chatgpt_review.ChatGPTUnavailable as exc:
            log.warning("ChatGPT unavailable for a moment: %s", exc.message)
            ctx.note("Not used: " + exc.message.split(",")[0])
            ctx.say("chatgpt", f"Could not check your moment: {exc.message.split(',')[0].rstrip('.')}.")
        except JobCancelled:
            raise
        except Exception:  # noqa: BLE001  (an optional second opinion must never cost you the clip)
            log.warning("ChatGPT check of a moment failed", exc_info=True)
            ctx.say("chatgpt", "Could not check your moment: something unexpected went wrong.")
        timing["second"] = round(time.monotonic() - started, 1)

    # ---- save and render ---------------------------------------------------------------------------
    ctx.start("save")
    settings = opts.clip_settings()
    settings["captions"]["censor"] = bool(cfg["captions.censor"])
    with db.session_scope() as s:
        clip = Clip(video_id=video.id, project_id=video.project_id, storage_key=storage.new_key(), origin="manual",
                    title=highlight.title[:300], hook_text=highlight.hook_text[:300], hook_options=highlight.hook_options,
                    description=highlight.description, hashtags=highlight.hashtags, category=highlight.category,
                    transcript_text=highlight.transcript_text, start=highlight.start, end=highlight.end,
                    score=highlight.score, subscores=highlight.subscores, confidence=highlight.confidence,
                    explanation=highlight.explanation, evidence=highlight.evidence, analysis_source="manual", rank=0,
                    settings=settings, cover_text=highlight.extra.get("cover_text", ""),
                    cover_word=highlight.extra.get("cover_word", ""))
        s.add(clip)
        s.flush()
        enqueue_render(s, clip, priority=8)
        clip_id = clip.id
    ctx.say("forge", f"Saved your clip from {_clock(highlight.start)} to {_clock(highlight.end)} "
                     f"({highlight.end - highlight.start:.0f} s) and started rendering it.")
    return {"clip_id": clip_id, "note": note, "timing": timing}


def get_clip_dir(clip: Clip) -> Path:
    from ..config import get_settings
    return get_settings().clips_dir / clip.storage_key


# ----------------------------------------------------------------------------------------------- render
def _render(ctx: JobContext, job: dict[str, Any]) -> dict[str, Any]:
    with db.session_scope() as s:
        clip = s.get(Clip, job["clip_id"])
        if clip is None:
            raise NotFound("This clip no longer exists")
        video = s.get(Video, clip.video_id)
        job["previous_render_status"] = "ready" if clip.file_path else "none"
        clip.render_status, clip.render_error = "rendering", ""
        cfg = settings_store.get_all(s)
        encoder = resolve_encoder(s)
        words = load_words(s, video.id, clip.start - 2.0, clip.end + 2.0)
        s.flush()
        s.expunge(clip)
        s.expunge(video)
    source = storage.video_source_path(video)
    st = clip.settings or {}
    captions_cfg = dict(st.get("captions") or {})
    hook_cfg = dict(st.get("hook") or {})
    audio_cfg = dict(st.get("audio") or {})
    aspect = st.get("aspect") if st.get("aspect") in ASPECTS else "9:16"
    layout = st.get("layout") if st.get("layout") in LAYOUTS else "auto"
    segments = clip_segments(clip.start, clip.end, words, bool(st.get("remove_silence")),
                             min_gap=float(st.get("silence_threshold", 0.8)), cuts=st.get("cuts"))
    total = sum(b - a for a, b in segments)

    ctx.start("framing")
    manual = st.get("subject_x") is not None or bool(st.get("facecam_box"))
    needs_faces = layout in ("auto", "split") or (layout in ("fill", "facecam_top") and not manual)
    faces = (analyze_faces(source, clip.start, clip.end - clip.start, video.width, video.height,
                           should_cancel=ctx.cancelled) if needs_faces else FaceSummary())
    plan = plan_layout(video.width, video.height, aspect, layout, faces, subject_x=st.get("subject_x"),
                       subject_y=st.get("subject_y"), zoom=float(st.get("zoom") or 1.0),
                       facecam_box=tuple(st["facecam_box"]) if st.get("facecam_box") else None,
                       gameplay_box=tuple(st["gameplay_box"]) if st.get("gameplay_box") else None,
                       preset=str(st.get("preset") or "auto"))
    ctx.note(plan.reason)

    ctx.start("captions")
    if clip.captions is not None:
        caption_words = [CaptionWord.from_dict(d) for d in clip.captions]
    else:
        caption_words = words_for_clip(words, segments)
        mark_emphasis(caption_words)
    ass_text, report = None, {}
    # Subtitles and the hook title have a switch each: subtitles off still leaves a hook title that is on.
    shown_words = caption_words if captions_cfg.get("enabled", True) else []
    hook_on = bool(hook_cfg.get("enabled", True) and clip.hook_text)
    if shown_words or hook_on:
        style = resolve_style({"censor": cfg["captions.censor"], **captions_cfg})
        ass_text, report = build_ass(
            shown_words, style, plan.out_w, plan.out_h,
            hook=clip.hook_text if hook_on else None,
            hook_seconds=float(hook_cfg.get("seconds", 4.0)), clip_seconds=total,
            position_y=captions_cfg.get("position", plan.caption_y) if "position" in captions_cfg else plan.caption_y,
            hook_y=plan.hook_y)

    ctx.start("encode", "Rendering video")
    folder = storage.clip_dir(clip)
    final, temp = folder / "clip.mp4", folder / "clip.rendering.mp4"
    request = RenderRequest(source=source, out_path=temp, work_dir=folder / "work", segments=segments,
                            src_w=video.width, src_h=video.height, has_audio=video.has_audio, plan=plan,
                            ass_text=ass_text, fps=int(st.get("fps") or 30), encoder=encoder,
                            quality=str(st.get("quality") or cfg["processing.priority"]),
                            normalize_audio=bool(audio_cfg.get("normalize", cfg["audio.normalize"])),
                            denoise=bool(audio_cfg.get("denoise", False)))
    progress = lambda f: ctx.progress(f, f"Rendering video ({f:.0%})")  # noqa: E731
    try:
        result = render_clip(request, on_progress=progress, should_cancel=ctx.cancelled)
    except ffmpeg.FFmpegError:
        if request.encoder == "libx264":
            raise
        log.warning("GPU encode failed, retrying on the CPU", extra={"clip_id": clip.id})
        request.encoder = "libx264"
        result = render_clip(request, on_progress=progress, should_cancel=ctx.cancelled)
    final = storage.place_file(temp, final)  # under its own name if the old video is open in the browser

    ctx.start("checks")
    checks = run_checks(final, expected_w=plan.out_w, expected_h=plan.out_h, expected_duration=total,
                        source_has_audio=video.has_audio, caption_report=report if ass_text else None,
                        cuts_into_word=cut_into_word(clip.start, clip.end, words))
    cover = ffmpeg.grab_frame(final, min(total * 0.35, max(0.0, total - 0.2)), folder / "cover.jpg", width=540)
    (folder / "captions.srt").write_text(build_srt(caption_words), encoding="utf-8")
    (folder / "captions.vtt").write_text(build_vtt(caption_words), encoding="utf-8")
    if ass_text:
        (folder / "captions.ass").write_text(ass_text, encoding="utf-8")
    else:
        (folder / "captions.ass").unlink(missing_ok=True)  # nothing burned in: no script from an earlier render
    shutil.rmtree(folder / "work", ignore_errors=True)

    ctx.start("cover")
    words, coloured = (clip.cover_text, clip.cover_word) if clip.cover_text else cover_words(clip.title)
    chosen = clip.cover_time if clip.cover_time is not None and any(
        a - 0.01 <= clip.cover_time <= b + 0.01 for a, b in segments) else None  # never from a part left out
    try:
        picture, moment = make_cover(source, clip.start, clip.end, plan.as_dict(), (video.width, video.height),
                                     words, coloured, folder, at=chosen, inside=segments,
                                     should_cancel=ctx.cancelled)
    except JobCancelled:
        raise
    except Exception:  # noqa: BLE001  (a thumbnail must never cost you the clip)
        log.warning("The thumbnail could not be made", exc_info=True, extra={"clip_id": clip.id})
        picture, moment = None, None

    with db.session_scope() as s:
        stored = s.get(Clip, clip.id)
        stored.render_status, stored.render_error = "ready", ""
        if picture is not None:
            stored.cover_path, stored.cover_time = storage.rel_path(picture), moment
        stored.file_path, stored.thumb_path = storage.rel_path(final), storage.rel_path(cover)
        stored.file_size = final.stat().st_size
        stored.out_width, stored.out_height, stored.out_duration = result.width, result.height, result.duration
        stored.qc = checks
        stored.rendered_at = db.utcnow()
        stored.render_info = {
            "layout": plan.layout, "plan": plan.as_dict(), "faces": faces.as_dict(), "encoder": result.encoder,
            "seconds": result.seconds_taken, "segments": [list(seg) for seg in segments], "captions": report,
            "caption_words": len(caption_words), "qc": summarize(checks)}
        (folder / "post.txt").write_text(build_post_kit(stored, s.get(Video, stored.video_id)), encoding="utf-8")
        still_working = s.scalar(select(Job.id).where(Job.type == "render", Job.video_id == stored.video_id,
                                                      Job.status.in_(("queued", "running")), Job.id != ctx.job_id))
        if still_working is None:
            ready = s.query(Clip).filter(Clip.video_id == stored.video_id, Clip.render_status == "ready").count()
            notify(s, "render_done", f"{ready} clip{'s' if ready != 1 else ''} ready from “{video.title}”",
                   "Preview, approve and download them in the AI Clipper.", f"/clipper?video={stored.video_id}")
    for old in folder.glob("clip*.mp4"):
        if old not in (final, temp):
            try:
                storage.remove_file(old)  # an earlier render; one still open in the browser goes next time
            except OSError:
                pass
    passed = sum(1 for check in checks if check.get("status") == "pass")
    ctx.say("forge", f"Rendered “{clip.title}” ({result.duration:.0f} s, {plan.out_w}×{plan.out_h}) on the "
                     f"{'graphics card' if result.encoder == 'h264_nvenc' else 'processor'} in "
                     f"{result.seconds_taken:.0f} s; {passed} of {len(checks)} checks passed.")
    if picture is not None and can_draw(words):
        ctx.say("forge", f"Made the thumbnail “{words}” from the moment {moment - clip.start:.0f} s into the clip.")
    elif picture is not None:
        ctx.say("forge", f"Made the thumbnail from the moment {moment - clip.start:.0f} s into the clip, without "
                         f"words: the fonts on this PC cannot draw “{words}”.")
    else:
        ctx.say("forge", "Could not make the thumbnail this time (the clip itself is fine); you can make it again "
                         "from the clip.")
    return {"file": storage.rel_path(final), "seconds": result.seconds_taken, "qc": summarize(checks),
            "encoder": result.encoder}


# ---------------------------------------------------------------------------------------------- dispatch
# ----------------------------------------------------------------------------------- import from a link
def _import(ctx: JobContext, job: dict[str, Any]) -> dict[str, Any]:
    from ..services import downloader  # yt-dlp is only loaded when a link is actually imported

    payload = job["payload"]
    settings = get_settings()
    key = str(payload.get("storage_key") or storage.new_key())
    folder = settings.uploads_dir / key

    def found_it(found: downloader.LinkInfo) -> None:
        free = shutil.disk_usage(settings.data_dir).free
        needed = (found.approx_bytes or 0) * 1.15 + 300 * 1024**2  # room to join picture and sound, and to work
        if found.approx_bytes and needed > free:
            raise ClipForgeError(
                f"There is not enough free disk space for this video: about {needed / 1024**3:.1f} GB is needed and "
                f"{free / 1024**3:.1f} GB is free.",
                hint="Free some space, choose a lower quality, or keep ClipForge's data on a bigger drive "
                     "(CLIPFORGE_DATA_DIR in .env).")
        ctx.note(found.title)
        ctx.start("download", f"Downloading “{found.title}”")

    ctx.start("inspect")
    path, found = downloader.download_link(
        payload["url"], folder, max_height=int(payload.get("max_height") or 1080),
        max_bytes=settings.max_upload_bytes, on_found=found_it,
        on_progress=lambda f: ctx.progress(f, f"Downloading the video ({f:.0%})"),
        should_cancel=ctx.cancelled, keep_partial=SHUTDOWN.is_set)

    try:
        ctx.start("register")
        info = ffmpeg.probe(path)
        with db.session_scope() as s:
            project_id = payload.get("project_id")
            if project_id is not None and s.get(Project, project_id) is None:
                project_id = None  # the project was deleted while the video was downloading
            creator = str(payload.get("creator_name") or found.creator or "")[:200]
            name = safe_filename(found.title, default="video", max_length=120) + path.suffix.lower()
            video, duplicate = register_video(
                s, path, key=key, mode="managed", original_name=name, title=payload.get("title") or found.title,
                info=info, thumb_folder=folder, source_type="url", source_url=found.url, creator_name=creator,
                permission_status=str(payload.get("permission_status") or "unknown"),
                permission_note=str(payload.get("permission_note") or ""),
                attribution=f"Clip from {creator}" if creator else "", project_id=project_id)
            video_id, title = video.id, video.title
            s.get(Job, ctx.job_id).video_id = video_id  # so the import shows in that video's work log
            notify(s, "import_done", f"Imported “{title}”",
                   (f"From {found.site}. " if found.site and found.site != "Generic" else "")
                   + ("The same video was already in your library. " if duplicate else "")
                   + "Ready to find clips.", f"/clipper?video={video_id}")
            if payload.get("analyze"):
                enqueue_analysis(s, video, dict(payload["analyze"]))
    except BaseException:
        if not SHUTDOWN.is_set():
            shutil.rmtree(folder, ignore_errors=True)
        raise
    size = path.stat().st_size if path.exists() else 0
    ctx.say("forge", f"Downloaded “{title}”" + (f" from {found.site}" if found.site and found.site != "Generic" else "")
                     + f" ({size / 1024**2:.0f} MB, {_clock(info.duration)})"
                     + (f", continuing after {found.retries} refusal{'s' if found.retries != 1 else ''} part-way,"
                        if found.retries else "")
                     + " and added it to your library"
                     + (", then queued it to find clips." if payload.get("analyze") else "."))
    log.info("video imported from a link", extra={"video_id": video_id, "site": found.site,
                                                  "seconds": round(info.duration, 1)})
    return {"video_id": video_id, "title": title, "duplicate_of": duplicate, "site": found.site,
            "note": f"Imported “{title}”"}


def _publish(ctx: JobContext, job: dict[str, Any]) -> dict[str, Any]:
    from ..publishing.service import run_publish  # imported here: publishing builds on this module
    return run_publish(ctx, job)


HANDLERS = {"analyze": _analyze, "render": _render, "import": _import, "moment": _moment, "publish": _publish}


def _record_failure(job: dict[str, Any], message: str) -> None:
    with db.session_scope() as s:
        if job["type"] == "analyze" and job.get("video_id"):
            video = s.get(Video, job["video_id"])
            if video is not None:
                video.status, video.last_error = "failed", message
                notify(s, "job_failed", f"Analysis failed for “{video.title}”", message, f"/clipper?video={video.id}")
        elif job["type"] == "render" and job.get("clip_id"):
            clip = s.get(Clip, job["clip_id"])
            if clip is not None:
                clip.render_status, clip.render_error = "failed", message
                notify(s, "job_failed", f"Rendering failed for “{clip.title}”", message, f"/editor/{clip.id}")
        elif job["type"] == "import":
            notify(s, "job_failed", "A video could not be imported from its link", message, "/clipper")
        elif job["type"] == "publish":
            from ..models import Post
            post = s.get(Post, int((job.get("payload") or {}).get("post_id") or 0))
            if post is not None and post.status == "publishing":  # stopped by something the job did not expect
                post.status, post.error, post.next_run_at = "failed", message, None
                notify(s, "job_failed", f"A post failed: “{post.clip_title}”", message, "/publishing")
        elif job["type"] == "moment":
            at = _clock(float((job.get("payload") or {}).get("start") or 0))
            notify(s, "job_failed", f"Your moment at {at} could not be made into a clip", message,
                   f"/clipper?video={job.get('video_id')}")


def _record_cancel(job: dict[str, Any]) -> None:
    with db.session_scope() as s:
        if job["type"] == "analyze" and job.get("video_id"):
            video = s.get(Video, job["video_id"])
            if video is not None:
                previous = job.get("previous_status", "ready")
                video.status = previous if previous in ("ready", "analyzed") else "ready"
        elif job["type"] == "render" and job.get("clip_id"):
            clip = s.get(Clip, job["clip_id"])
            if clip is not None:
                clip.render_status = job.get("previous_render_status", "none")
                storage.remove_file(get_clip_dir(clip) / "clip.rendering.mp4")
        elif job["type"] == "publish":
            from ..db import utcnow
            from ..models import Post
            post = s.get(Post, int((job.get("payload") or {}).get("post_id") or 0))
            if post is not None and post.status in ("publishing", "scheduled"):
                if SHUTDOWN.is_set():  # the app is closing: carry on from the saved upload next time
                    post.status, post.next_run_at = "scheduled", utcnow()
                else:
                    post.status, post.next_run_at, post.detail = "cancelled", None, "Cancelled while uploading."


def run_job(job_id: int) -> None:
    """Run one claimed job to its end and record the outcome. Never raises."""
    with db.session_scope() as s:
        row = s.get(Job, job_id)
        if row is None:
            return
        job = {"id": row.id, "type": row.type, "payload": dict(row.payload or {}), "video_id": row.video_id,
               "clip_id": row.clip_id}
    handler = HANDLERS.get(job["type"])
    ctx = JobContext(job_id, job["type"]) if handler else None
    started = time.monotonic()
    try:
        if handler is None:
            raise ClipForgeError(f"Unknown job type: {job['type']}")
        result = handler(ctx, job)
        if ctx.worklog:
            result = {**result, "log": ctx.worklog}
        ctx.finish()
        with db.session_scope() as s:
            queue.finish(s, job_id, result)
            if result.get("note"):
                s.get(Job, job_id).message = result["note"]
        log.info("job finished", extra={"job_id": job_id, "type": job["type"],
                                        "seconds": round(time.monotonic() - started, 1)})
    except JobCancelled:
        _record_cancel(job)
        with db.session_scope() as s:
            if SHUTDOWN.is_set() and not queue.is_cancel_requested(s, job_id):
                row = s.get(Job, job_id)
                row.status, row.worker_id, row.message = "queued", "", "Paused when the app closed; will continue"
                if job["type"] == "render" and job.get("clip_id") and s.get(Clip, job["clip_id"]) is not None:
                    s.get(Clip, job["clip_id"]).render_status = "queued"
                if job["type"] == "analyze" and job.get("video_id") and s.get(Video, job["video_id"]) is not None:
                    s.get(Video, job["video_id"]).status = "analyzing"
            else:
                queue.mark_cancelled(s, job_id, "Cancelled")
                if ctx is not None:   # keep the lines of what was already done
                    s.get(Job, job_id).result = {"log": [*ctx.worklog, {"actor": "forge", "text": "Cancelled."}]}
        log.info("job stopped", extra={"job_id": job_id, "type": job["type"], "shutdown": SHUTDOWN.is_set()})
    except Exception as exc:  # noqa: BLE001  (a job must never take the worker down)
        if isinstance(exc, ClipForgeError):
            message = exc.message + (f" {exc.hint}" if exc.hint else "")
        elif isinstance(exc, ffmpeg.FFmpegError):
            message = f"FFmpeg failed: {exc.stderr_tail or exc}"
        else:
            message = f"Unexpected error ({type(exc).__name__}): {exc}"
        detail = traceback.format_exc()
        log.error("job failed: %s", message, extra={"job_id": job_id, "type": job["type"]}, exc_info=True)
        try:
            _record_failure(job, message)
        finally:
            with db.session_scope() as s:
                queue.fail(s, job_id, message, detail[-6000:])
                if ctx is not None:
                    row = s.get(Job, job_id)
                    row.result = {"log": [*ctx.worklog, {"actor": "forge", "text": f"Stopped: {message}"[:300]}]}
