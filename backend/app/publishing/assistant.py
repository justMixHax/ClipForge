"""The publishing assistant: Claude suggests a title, a caption and hashtags for a clip from what is really said
in it. Accurate and without clickbait the clip does not deliver; the creator credit stays. It uses the same Claude
Code tool and your Claude plan as the clip picker, and nothing is sent when Settings say local rules only."""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from ..analysis import claude_review
from ..errors import ClipForgeError, DependencyMissing, NotFound
from ..models import Clip, Video
from ..services import settings_store
from . import store
from .service import clean_tags

WHAT = {
    "youtube": ("a YouTube Short", "title: up to 90 characters, no < or >. caption: one to three short sentences for "
                                   "the description, then the credit line."),
    "tiktok": ("a TikTok post", "title: a short headline, up to 90 characters. caption: one or two short sentences "
                                "with the credit line."),
    "instagram": ("an Instagram Reel", "title: a short headline, up to 90 characters. caption: one to three short "
                                       "sentences with the credit line."),
}


def build_prompt(clip: Clip, video: Video | None, platform: str) -> str:
    kind, shape = WHAT[platform]
    creator = video.creator_name if video is not None else ""
    credit = (video.attribution if video is not None and video.attribution else
              (f"Clip from {creator}" if creator else ""))
    source = f'"{video.title}"' + (f" by {creator}" if creator else "") if video is not None else "unknown"
    return f"""You write the text for {kind} (platform: {store.LABELS[platform]}) made by ClipForge from a longer video.
Base it only on what is said in the clip (the transcript below) and the facts given here. Never invent quotes,
events, numbers or claims, and no clickbait the clip does not deliver. Keep the meaning of what the speakers say.

Clip title so far: "{clip.title}"
On-screen hook: "{clip.hook_text}"
Category: {clip.category or "not set"}
Source video: {source}
Credit line to keep, word for word: "{credit or 'none'}"

Transcript of the clip:
{(clip.transcript_text or "(nothing is said in this clip)")[:4000]}

Write: {shape} hashtags: three to six relevant hashtags, lowercase, without spaces.

Answer with JSON only: {{"title": "...", "caption": "...", "hashtags": ["#..."]}}"""


def parse(printed: str) -> dict[str, Any]:
    try:
        envelope = json.loads(printed)
    except ValueError:
        envelope = {"result": printed}
    if isinstance(envelope, dict) and envelope.get("is_error"):
        raise ClipForgeError(f"Claude could not write the text: {str(envelope.get('result') or '')[:200]}")
    text = str(envelope.get("result") or "") if isinstance(envelope, dict) else ""
    match = re.search(r"\{.*\}", text, flags=re.S)
    try:
        data = json.loads(match.group(0)) if match else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise ClipForgeError("Claude's answer could not be read. Try again.")
    title = " ".join(str(data.get("title") or "").replace("<", "").replace(">", "").split())[:100]
    caption = str(data.get("caption") or "").strip()[:2200]
    hashtags = clean_tags([str(t) for t in data.get("hashtags") or [] if isinstance(t, (str, int))])[:8]
    if not title and not caption:
        raise ClipForgeError("Claude did not suggest any text. Try again.")
    return {"title": title, "caption": caption, "hashtags": hashtags}


def suggest(session: Session, clip_id: int, platform: str) -> dict[str, Any]:
    store.check_platform(platform)
    clip = session.get(Clip, clip_id)
    if clip is None:
        raise NotFound("That clip does not exist.")
    cfg = settings_store.get_all(session)
    if cfg["ai.engine"] != "claude":
        raise ClipForgeError("Claude is switched off in Settings (local rules only), so nothing is sent to it.",
                             hint="Switch the AI engine to Claude in Settings to get suggestions.")
    prompt = build_prompt(clip, session.get(Video, clip.video_id), platform)
    try:
        printed = claude_review.run_claude(prompt, model=str(cfg["ai.claude_model"]), timeout=180)
    except claude_review.ClaudeUnavailable as exc:
        raise DependencyMissing(exc.message.split(",")[0].rstrip(".") + ".", hint=exc.hint) from exc
    return parse(printed)
