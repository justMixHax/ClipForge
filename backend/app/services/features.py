"""An honest status for every feature the product is meant to have.

working      built and verified on this PC
needs_setup  built, but something has to be installed or configured first (the detail says what)
unavailable  built, but this PC does not have the hardware for it (the detail says what is used instead)
blocked      not built, and it cannot work until an outside party approves or provides something
planned      not built yet

The app shows this list as-is, so nothing in the interface claims to work when it does not. A feature
that is not built is always "planned" or "blocked": storing an API key or choosing a provider for it
never moves it to "needs setup".
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..analysis import chatgpt_review, claude_review
from ..editing.reframe import detector_available
from ..transcription import models
from . import downloader, hardware


def _item(key: str, area: str, name: str, status: str, detail: str) -> dict[str, str]:
    return {"key": key, "area": area, "name": name, "status": status, "detail": detail}


NEEDS = {
    "youtube": "Built. Needs your own Google Cloud project with the YouTube Data API v3 and a Desktop OAuth client "
               "(free), then Connect under Publishing → Accounts.",
    "tiktok": "Built for drafts: clips go to your TikTok inbox and you finish the post in the TikTok app. Needs a "
              "TikTok developer app with Login Kit (desktop) and the Content Posting API, then Connect.",
    "instagram": "Built for Reels. Needs a professional Instagram account linked to a Facebook Page, your own Meta "
                 "app, and a token from Meta's Graph API Explorer.",
}
PUBLISH_NAMES = {"youtube": ("publish_youtube", "YouTube Shorts upload"),
                 "tiktok": ("publish_tiktok", "TikTok drafts (upload to inbox)"),
                 "instagram": ("publish_instagram", "Instagram Reels publishing")}


def _publishing(session: Session) -> list[dict[str, str]]:
    """A platform counts as working only once it has confirmed a real post made from this PC."""
    from ..models import Account, Post
    from ..publishing import store

    confirmed = ("published", "scheduled_on_platform", "private", "in_drafts")
    items, total = [], 0
    for platform, (key, name) in PUBLISH_NAMES.items():
        done = session.query(Post).filter(Post.platform == platform, Post.status.in_(confirmed)).count()
        total += done
        connected = session.query(Account).filter(Account.platform == platform, Account.status == "connected").all()
        label = store.LABELS[platform]
        if done:
            status, detail = "working", f"Confirmed by {label} for posts made from this PC ({done})."
        elif connected:
            status, detail = "needs_setup", (f"Connected as {', '.join(a.name for a in connected)}. Not tested with a "
                                             "real post yet.")
        elif store.app_summary(session, platform)["configured"]:
            status, detail = "needs_setup", ("Your Meta app is set up: paste a token under Publishing → Accounts."
                                             if platform == "instagram" else
                                             "Your app is set up: press Connect under Publishing → Accounts.")
        else:
            status, detail = "needs_setup", NEEDS[platform]
        items.append(_item(key, "Publishing", name, status, detail))
    items.append(_item("publish_tiktok_direct", "Publishing", "TikTok direct posting", "blocked",
                       "Not offered: TikTok's rules for direct posting exclude tools for posting to your own accounts, "
                       "and apps it has not audited may only post privately. Drafts work instead."))
    items.append(_item("scheduling", "Publishing", "Publishing queue, scheduler and calendar",
                       "working" if total else "needs_setup",
                       "Built, and confirmed by real posts from this PC." if total else
                       "Built: the queue, calendar and scheduler run on this PC. Posts go out while ClipForge is "
                       "running; YouTube schedules by itself. Connect an account to use it."))
    return items


def feature_status(session: Session) -> list[dict[str, Any]]:
    installed = models.installed_models()
    hw = hardware.detect()
    cuda, nvenc = hw.get("whisper_cuda", {}), hw.get("encoders", {}).get("h264_nvenc", {})
    gpu = bool(hw.get("gpus"))

    return [
        _item("upload", "Clipping", "Upload or link a video", "working",
              "MP4, MOV, MKV, WebM and M4V. Large files can be linked from disk without copying."),
        _item("transcription", "Clipping", "Speech-to-text with word timings",
              "working" if installed else "needs_setup",
              f"Local faster-whisper. Installed: {', '.join(installed)}." if installed
              else "No speech model is installed. Install one in Settings, or run scripts\\setup.ps1."),
        _item("gpu_transcription", "Clipping", "GPU speech-to-text",
              "working" if cuda.get("ok") else ("needs_setup" if gpu else "unavailable"),
              (cuda.get("detail", "") or "Not tested yet.") if gpu
              else "No NVIDIA graphics card was found, so speech-to-text runs on the processor."),
        _item("highlights", "Clipping", "Highlight discovery and 12-part scoring", "working",
              "Speech, loudness against the local baseline, pauses, pacing, picture activity and scene cuts. "
              "Where almost nothing is said, loud and busy moments fill the free places, marked low confidence. "
              "English cue lists; other languages use the non-text signals with lower confidence."),
        _item("claude_engine", "Clipping", "Claude picks the clips",
              "working" if claude_review.find_cli() else "needs_setup",
              "Claude reads the transcript and picks the moments, through the Claude Code tool on this PC and your "
              "own Claude plan (no API key). If it is signed out or the plan limit is reached, the local rules pick "
              "and the job says why. Switch it off in Settings." if claude_review.find_cli()
              else "The Claude Code command-line tool was not found. Install it and sign in; until then the local "
                   "rules pick the clips."),
        _item("moments", "Clipping", "Clips from your own timestamps", "working",
              "Type where a moment starts (or paste a link copied at that moment), or mark it in the player; "
              "Claude ends it after the payoff and names it, then the usual captions, render and checks follow."),
        _item("chatgpt_second_opinion", "Clipping", "Second opinion: ChatGPT",
              "working" if chatgpt_review.find_cli() else "needs_setup",
              "ChatGPT checks the picks and may add up to two missed moments, through the Codex tool from the "
              "ChatGPT app and your own ChatGPT plan (no API key). If it is signed out or the plan limit is "
              "reached, the picks are kept unchecked and the job says why. Switch it off in Settings."
              if chatgpt_review.find_cli() else
              "The Codex tool from the ChatGPT app was not found. Install the app and sign in; until then the "
              "picks are not double-checked."),
        _item("llm_pass", "Clipping", "Other AI providers (Ollama, OpenAI, Anthropic API)", "planned",
              "Not built. Claude through your plan is the AI engine; keys stored here are kept for later and no "
              "request is sent with them today."),
        _item("diarization", "Clipping", "Who-is-speaking detection", "planned",
              "Not built. Speaker changes are not used yet."),
        _item("render", "Clipping", "Vertical render with burned-in captions", "working",
              "9:16, 1:1, 4:5 and 16:9. Frame-accurate cuts, loudness normalised to -14 LUFS."),
        _item("gpu_encode", "Clipping", "GPU video encoding (NVENC)",
              "working" if nvenc.get("ok") else ("needs_setup" if gpu else "unavailable"),
              (nvenc.get("detail", "") or "Not tested yet.") if gpu
              else "No NVIDIA graphics card was found, so video is encoded on the processor (x264)."),
        _item("face_reframe", "Editing", "Face-aware reframing and facecam detection",
              "needs_setup" if not detector_available() else "working",
              "Face detector installed. Crops are fixed per clip; they do not yet follow a moving subject. "
              "Validated on synthetic tests only so far, so check the framing on real footage."
              if detector_available() else "The YuNet face detector model is missing; run scripts\\setup.ps1. "
                                           "Without it clips use the whole-frame layout."),
        _item("captions", "Editing", "Caption styles, emphasis, SRT and VTT", "working",
              "Six styles, editable words, automatic fit to the frame, safe-area placement; subtitles can be "
              "switched on or off per clip."),
        _item("editor", "Editing", "Trim, captions, layout, re-render", "working",
              "Trim on a waveform timeline, edit every caption word, change layout and style, leave parts out of "
              "the middle, undo and redo."),
        _item("prompt_edit", "Editing", "Prompt edit: describe a change in your own words",
              "working" if claude_review.find_cli() else "needs_setup",
              "Claude turns what you write into changes ClipForge can make (cut, parts left out, subtitles, hook, "
              "layout, sound, title, thumbnail), each checked within the editor's limits, and the clip is rendered "
              "again; one press undoes it. What it cannot do is said, never faked."
              if claude_review.find_cli() else
              "Needs the Claude Code command-line tool on this PC, signed in to your Claude plan."),
        _item("thumbnails", "Editing", "A thumbnail for every clip", "working",
              "Its best moment, framed like the clip, with two to six big words and one in colour, clear of faces "
              "and of TikTok's buttons. Words in Arabic, Hebrew and similar scripts are not drawn yet."),
        _item("silence_removal", "Editing", "Silence removal", "working", "Cuts long pauses down to a short breath."),
        _item("quality_checks", "Editing", "Export quality checks", "working",
              "Playback, size, length, audio, black frames, silence, caption overflow, clipped words."),
        _item("projects", "Library", "Projects", "working",
              "Group videos and their clips by series, client or campaign; filter the library by project."),
        _item("search", "Library", "Search inside videos", "working",
              "Keyword search over transcripts with timestamps. Meaning-based search is planned."),
        _item("duplicates", "Library", "Duplicate and similarity detection", "working",
              "Overlapping or re-told moments are not suggested twice; identical uploads are flagged."),
        _item("variants", "Library", "A/B variants", "working",
              "Duplicate a clip to try another hook, style or cut. Comparing their results needs analytics."),
        _item("feedback", "Library", "Feedback that adjusts ranking", "working",
              "Your marks shift score weights by fixed, capped amounts, shown in Settings."),
        _item("youtube_url", "Sources", "Import from a link",
              "working" if downloader.available() else "needs_setup",
              f"Public videos from YouTube, Twitch, Kick and most video sites (yt-dlp {downloader.version()}). No "
              "sign-in and no cookies, and nothing is downloaded until you have said why you may clip the video. "
              + (f"YouTube links use {downloader.js_runtime()}." if downloader.js_runtime()
                 else "YouTube links will not work until Deno or Node.js 22 or newer is installed "
                      "(winget install DenoLand.Deno); other sites do.")
              if downloader.available() else "The yt-dlp package is missing. Run scripts\\setup.ps1."),
        _item("discovery", "Sources", "YouTube creator discovery and watchlists", "planned",
              "Not built. It will use the official YouTube Data API with your own API key."),
        _item("automation", "Automation", "Automated workflows", "planned",
              "Not built. Batch processing through the queue works today."),
        *_publishing(session),
        _item("manual_export", "Publishing", "Manual export with post text", "working",
              "Download a clip or export to a folder with title, description, hashtags and source details."),
        _item("youtube_text", "Publishing", "YouTube title and description by Claude",
              "working" if claude_review.find_cli() else "needs_setup",
              "A Shorts title and a description from what is said in the clip, with the credit, #Shorts and "
              "hashtags, with or without emojis. Copy them into YouTube Studio; they are not saved."
              if claude_review.find_cli() else
              "Needs the Claude Code command-line tool on this PC, signed in to your Claude plan."),
        _item("tiktok_caption", "Publishing", "TikTok caption rewritten by Claude",
              "working" if claude_review.find_cli() else "needs_setup",
              "A new caption from what is said in the clip, with or without emojis and hashtags; the credit stays. "
              "You can change it and save it with the clip." if claude_review.find_cli() else
              "Needs the Claude Code command-line tool on this PC, signed in to your Claude plan. You can still "
              "write and save your own caption."),
        _item("analytics", "Analytics", "Performance tracking", "planned",
              "Not built. No views or engagement numbers are shown anywhere until they come from a real source."),
        _item("strategist", "Analytics", "AI content strategist", "planned", "Not built."),
        _item("notifications", "System", "Local notifications", "working",
              "In-app notifications when clips are ready or a job fails."),
        _item("resume", "System", "Resumable processing", "working",
              "Interrupted jobs continue from saved audio, transcript parts and signals."),
        _item("shutdown", "System", "Shut down button and desktop shortcut", "working",
              "Shut down ClipForge from its menu when it was started with start.ps1 or the desktop shortcut (a "
              "running job continues next time); scripts\\shortcut.ps1 puts a "
              "ClipForge shortcut on your desktop that starts it, or just opens it when it is already running."),
    ]
