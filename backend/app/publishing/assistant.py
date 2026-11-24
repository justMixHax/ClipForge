"""The publishing assistant: Claude suggests a title, a caption and hashtags for a clip from what is really said
in it. Accurate and without clickbait the clip does not deliver; the creator credit stays. It uses the same Claude
Code tool and your Claude plan as the clip picker, and nothing is sent when Settings say local rules only."""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from ..analysis import claude_review
from ..analysis.answers import json_answers
from ..analysis.describe import credit_line, merge_hashtags, post_caption, tidy_caption
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


# The YouTube Shorts text to copy into YouTube Studio: the credit and the hashtags are added by ClipForge.
YOUTUBE_SHAPE = ("title: up to 70 characters, no hashtags, no < or >. caption: the YouTube description, on separate "
                 "lines: what happens in the clip in one line, one short sentence more about the moment, then a short "
                 "question that invites viewers to comment. Leave the credit line and hashtags out of it. No < or > "
                 "anywhere.")


def build_prompt(clip: Clip, video: Video | None, platform: str, *, emojis: bool | None = None,
                 avoid: str = "", shape: str = "") -> str:
    """The request to Claude. emojis: whether the caption should have some (None: not said). avoid: a caption to
    write a different one from. shape: what to write, instead of the platform's usual."""
    kind, usual = WHAT[platform]
    shape = shape or usual
    style = ""
    if emojis is True:
        style += "\nUse one to three emojis in the caption that fit what happens."
    elif emojis is False:
        style += "\nNo emojis."
    if avoid:
        style += f'\nWrite a new caption, clearly different from this one: "{" ".join(avoid.split())[:400]}"'
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

Write: {shape} hashtags: three to six relevant hashtags, lowercase, without spaces.{style}

Answer with JSON only: {{"title": "...", "caption": "...", "hashtags": ["#..."]}}"""


def parse(printed: str) -> dict[str, Any]:
    try:
        envelope = json.loads(printed)
    except ValueError:
        envelope = {"result": printed}
    if isinstance(envelope, dict) and envelope.get("is_error"):
        raise ClipForgeError(f"Claude could not write the text: {str(envelope.get('result') or '')[:200]}")
    text = str(envelope.get("result") or "") if isinstance(envelope, dict) else ""
    # the last block first: a correction comes after its draft
    data = next((block for block in json_answers(text) if {"title", "caption", "hashtags"} & set(block)), None)
    if data is None:
        raise ClipForgeError("Claude's answer could not be read. Try again.")
    title = " ".join(str(data.get("title") or "").replace("<", "").replace(">", "").split())[:100]
    caption = str(data.get("caption") or "").strip()[:2200]
    hashtags = clean_tags([str(t) for t in data.get("hashtags") or [] if isinstance(t, (str, int))])[:8]
    if not title and not caption:
        raise ClipForgeError("Claude did not suggest any text. Try again.")
    return {"title": title, "caption": caption, "hashtags": hashtags}


def _clip_for_claude(session: Session, clip_id: int) -> tuple[Clip, dict[str, Any]]:
    clip = session.get(Clip, clip_id)
    if clip is None:
        raise NotFound("That clip does not exist.")
    cfg = settings_store.get_all(session)
    if cfg["ai.engine"] != "claude":
        raise ClipForgeError("Claude is switched off in Settings (local rules only), so nothing is sent to it.",
                             hint="Switch the AI engine to Claude in Settings to get suggestions.")
    return clip, cfg


def _ask(prompt: str, cfg: dict[str, Any]) -> dict[str, Any]:
    try:
        printed = claude_review.run_claude(prompt, model=str(cfg["ai.claude_model"]), timeout=180)
    except claude_review.ClaudeUnavailable as exc:
        raise DependencyMissing(exc.message.split(",")[0].rstrip(".") + ".", hint=exc.hint) from exc
    return parse(printed)


def suggest(session: Session, clip_id: int, platform: str) -> dict[str, Any]:
    store.check_platform(platform)
    clip, cfg = _clip_for_claude(session, clip_id)
    return _ask(build_prompt(clip, session.get(Video, clip.video_id), platform), cfg)


def tiktok_caption(session: Session, clip_id: int, *, emojis: bool = True, hashtags: bool = True) -> str:
    """A new TikTok caption by Claude, different from the one the clip has now: its words (with emojis or not),
    the credit line, then the hashtags if wanted. Hashtags among the words, and emojis when none are wanted, are
    taken out whatever Claude writes."""
    clip, cfg = _clip_for_claude(session, clip_id)
    video = session.get(Video, clip.video_id)
    credit = credit_line(video.attribution, video.creator_name) if video is not None else ""
    current = clip.tiktok_caption or post_caption(clip.description or clip.title, credit, clip.hashtags or [])
    answer = _ask(build_prompt(clip, video, "tiktok", emojis=emojis, avoid=current), cfg)
    words = answer["caption"] or answer["title"]
    if credit:  # the credit goes on a line of its own, wherever Claude put it
        words = re.sub(rf"\s*[-–—|•·:]?\s*{re.escape(credit)}\.?", " ", words, flags=re.IGNORECASE)
    words = tidy_caption(words, emojis=emojis)
    handle = re.sub(r"[^a-z0-9]", "", (video.creator_name if video is not None else "").lower())
    creator = [f"#{handle}"] if handle else []  # the creator's tag stays, however many Claude suggests
    tags = merge_hashtags(answer["hashtags"], [*creator, *(clip.hashtags or [])]) if hashtags else []
    return post_caption(words, credit, tags)


def youtube_text(session: Session, clip_id: int, *, emojis: bool = True, hashtags: bool = True,
                 avoid: str = "") -> dict[str, str]:
    """A YouTube Shorts title and description by Claude, to copy into YouTube Studio: the title without hashtags;
    the description's lines, the credit on a line of its own, then #Shorts and the hashtags if wanted. avoid: the
    description in the box now, so a new one is different. Nothing is saved."""
    clip, cfg = _clip_for_claude(session, clip_id)
    video = session.get(Video, clip.video_id)
    credit = credit_line(video.attribution, video.creator_name) if video is not None else ""
    answer = _ask(build_prompt(clip, video, "youtube", emojis=emojis, avoid=avoid, shape=YOUTUBE_SHAPE), cfg)

    def without_credit(text: str, joiner: str) -> str:  # the credit goes on a line of its own, wherever Claude put it
        return re.sub(rf"\s*[-–—|•·:]?\s*{re.escape(credit)}\.?", joiner, text, flags=re.IGNORECASE) if credit else text

    def clean(text: str) -> str:  # YouTube Studio refuses < and > in titles and descriptions
        return tidy_caption(re.sub(r"[<>]", "", text), emojis=emojis)

    title = clean(without_credit(answer["title"], " ")).replace("\n", " ") or " ".join(clip.title.split())
    if len(title) > 100:
        title = title[:99].rsplit(" ", 1)[0].rstrip(",;:-–— ") + "…"
    parts = [clean(without_credit(answer["caption"] or answer["title"], "\n")), re.sub(r"[<>]", "", credit)]
    if hashtags:
        handle = re.sub(r"[^a-z0-9]", "", (video.creator_name if video is not None else "").lower())
        creator = [f"#{handle}"] if handle else []
        mine = [t for t in answer["hashtags"] if t.lower() != "#shorts"]  # #Shorts is always put first
        parts.append(" ".join(["#Shorts", *merge_hashtags(mine, [*creator, *(clip.hashtags or [])])]))
    return {"title": title, "description": "\n\n".join(part for part in parts if part)[:5000]}
