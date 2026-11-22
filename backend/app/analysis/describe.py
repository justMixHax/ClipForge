"""Titles, on-screen hook lines and hashtags for a clip.

Everything here is extractive: titles and hooks are lines the speaker actually said, shortened at a word
boundary. Nothing is invented, so a hook can never claim something that did not happen. (An optional LLM
pass can rephrase them later; it is given the same rule.)
"""
from __future__ import annotations

import re

from .lexicon import DEPENDENT_FIRST, content_tokens
from .presets import GAME_STYLES
from .scoring import Scored, ScoringContext

TITLE_MAX = 70
HOOK_MAX_WORDS = 14
NON_LEXICAL = {"ha", "haha", "hahaha", "lol", "woo", "ah", "yes", "wow", "hey", "ooh", "whoa"}
CATEGORY_TAGS = {"funny": "#funny", "hype": "#clutch", "fail": "#fail", "rage": "#rage", "hot_take": "#hottake",
                 "debate": "#debate", "story": "#storytime", "surprise": "#unexpected", "reaction": "#reaction",
                 "action": "#gameplay"}
PRESET_TAGS = {"gaming": ["#gaming", "#gamingclips"], "streamer": ["#streamer", "#clips"],
               "podcast": ["#podcast", "#podcastclips"], "auto": ["#clips"]}
GAME_PATTERNS = {"gta": r"\bgta\b|grand theft auto|vice city", "dayz": r"\bday ?z\b",
                 "fc": r"\bea ?fc\b|\bfifa\b|ultimate team", "roblox": r"\broblox\b"}


def _trim_end(text: str) -> str:
    return text.strip().rstrip(".,;:…-–— ").strip()


def _sentence_text(ctx: ScoringContext, scored: Scored, k: int) -> str:
    """Sentence k as it is heard inside the clip (the first sentence loses its trimmed filler words)."""
    if k == scored.i:
        s = ctx.sentences[k]
        return " ".join(ctx.words[n].text.strip() for n in range(scored.first_word, s.w1) if ctx.words[n].text.strip())
    return ctx.sentences[k].text


def _natural_line(text: str, max_words: int = HOOK_MAX_WORDS) -> str | None:
    """The text as an on-screen line: whole if short enough, otherwise cut at the last comma or full stop
    that fits. Returns None when there is no natural place to stop, rather than cutting mid-phrase."""
    words = text.split()
    if len(words) <= max_words:
        return _trim_end(text)
    for n in range(max_words, 2, -1):
        if words[n - 1][-1:] in ",;:.!?":
            return _trim_end(" ".join(words[:n]))
    return None


def _usable_hook(line: str) -> bool:
    tokens = line.split()
    if len(tokens) < 3 or tokens[0].lower().strip(".,!?") in DEPENDENT_FIRST:
        return False
    return any(t not in NON_LEXICAL for t in content_tokens(line))


def build_title(ctx: ScoringContext, scored: Scored) -> str:
    opening = _sentence_text(ctx, scored, scored.i)
    opening_cues = ctx.cues[scored.i]
    strong_opening = len(opening.split()) >= 4 and (opening_cues.question or opening_cues.hook > 0
                                                    or opening_cues.hot_take > 0)
    if strong_opening:
        text = opening
    else:
        # otherwise the most telling line: strongest by cues, with a few words to carry it
        best = max(range(scored.i, scored.j + 1),
                   key=lambda k: (ctx.text_strength[k] + (0.5 if 5 <= ctx.n_words[k] <= 16 else 0.0), -k))
        text = _sentence_text(ctx, scored, best)
        if len(text.split()) < 4:
            text = opening if len(opening.split()) >= 4 else text
    text = text.strip()
    if text.endswith((".", ",", ";", ":")):
        text = _trim_end(text)
    if len(text) > TITLE_MAX:
        cut = text[:TITLE_MAX].rsplit(" ", 1)[0]
        text = _trim_end(cut) + "…"
    return text[:1].upper() + text[1:] if text else "Untitled clip"


def build_hooks(ctx: ScoringContext, scored: Scored) -> list[str]:
    """Up to four different on-screen hook lines, each a direct quote from the clip: the opening line,
    the payoff line, then the strongest other lines."""
    i, j = scored.i, scored.j
    opening = _sentence_text(ctx, scored, i)
    k = i
    while len(opening.split()) < 3 and k < j:  # "Wait." alone is not a hook; carry on into the next line
        k += 1
        opening = f"{opening} {ctx.sentences[k].text}"
    strongest = sorted(range(i, j + 1), key=lambda n: -(ctx.text_strength[n] + ctx.cues[n].hook + ctx.cues[n].hot_take))
    candidates = [opening, _sentence_text(ctx, scored, scored.peak_sentence)]
    candidates += [_sentence_text(ctx, scored, n) for n in strongest]

    out: list[str] = []
    seen: set[str] = set()
    for text in candidates:
        line = _natural_line(text)
        if not line or not _usable_hook(line):
            continue
        key = re.sub(r"[^a-z0-9 ]", "", line.lower())
        if key not in seen and not any(key in other for other in seen):
            seen.add(key)
            out.append(line)
        if len(out) == 4:
            break
    if not out:  # nothing clean enough: fall back to the opening words as spoken
        out.append(_trim_end(" ".join(opening.split()[:HOOK_MAX_WORDS])) or "Watch this")
    return out


def detect_game(text: str) -> str:
    lowered = text.lower()
    for key, pattern in GAME_PATTERNS.items():
        if re.search(pattern, lowered):
            return key
    return ""


def build_hashtags(category: str, preset: str, game_style: str, creator: str, text: str) -> list[str]:
    tags: list[str] = []
    game = game_style if game_style in GAME_STYLES and game_style != "other" else detect_game(text)
    if game and GAME_STYLES[game]["hashtag"]:
        tags.append(GAME_STYLES[game]["hashtag"])
    handle = re.sub(r"[^a-z0-9]", "", creator.lower())
    if handle:
        tags.append(f"#{handle}")
    if category in CATEGORY_TAGS:
        tags.append(CATEGORY_TAGS[category])
    tags += PRESET_TAGS.get(preset, PRESET_TAGS["auto"])
    tags.append("#fyp")
    return list(dict.fromkeys(tags))[:6]


def build_description(title: str, creator: str) -> str:
    return f"{title}\n\nClip from {creator}" if creator else title
