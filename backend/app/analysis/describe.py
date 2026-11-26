"""Titles, on-screen hook lines, hashtags, thumbnail words and the post caption for a clip.

Everything here is extractive: titles and hooks are lines the speaker actually said, shortened at a word
boundary. Nothing is invented, so a hook can never claim something that did not happen. (An optional LLM
pass can rephrase them later; it is given the same rule.)
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from typing import Any

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
    # Never a general tag (#fyp, #viral): it sends the clip to everyone, specific ones to people who would watch it.
    return list(dict.fromkeys(tags))[:6]


def build_description(title: str, creator: str) -> str:
    return f"{title}\n\nClip from {creator}" if creator else title


def merge_hashtags(first: Sequence[str], then: Sequence[str], limit: int = 6) -> list[str]:
    """The clip's own hashtags (at most four, so the creator and game still fit), then the usual ones; each once,
    whatever its case, and at most six in all."""
    out: list[str] = []
    for tag in [*list(first)[:4], *then]:
        if tag.lower() not in {t.lower() for t in out}:
            out.append(tag)
    return out[:limit]


def clean_hashtags(raw: Any, limit: int = 8) -> list[str]:
    """Hashtags from a list or a line of text, made safe: # followed by 2 to 30 letters, digits or underscores
    (spaces inside one are dropped), with at least one letter, each once whatever its case."""
    items = raw if isinstance(raw, (list, tuple)) else re.split(r"[\s,]+", str(raw or ""))
    out: list[str] = []
    for item in items:
        body = re.sub(r"[^\w]", "", str(item))
        if 2 <= len(body) <= 30 and re.search(r"[^\W\d_]", body) and f"#{body}".lower() not in {t.lower() for t in out}:
            out.append(f"#{body}")
    return out[:limit]


HASHTAG_IN_TEXT = re.compile(r"#(?=\w*[^\W\d_])\w+")
CAPTION_MAX = 220


def clean_caption(text: Any) -> tuple[str, list[str]]:
    """(the caption as one line of at most 220 characters, cut at a word; the hashtags that were in it). A number
    sign before a number ("the #1 cop") is not a hashtag and stays."""
    text = str(text or "")
    found = HASHTAG_IN_TEXT.findall(text)
    text = " ".join(HASHTAG_IN_TEXT.sub(" ", text).split())
    if len(text) > CAPTION_MAX:
        text = text[:CAPTION_MAX - 1].rsplit(" ", 1)[0].rstrip(",;:-–— ") + "…"
    return text, found


def _emoji_part(ch: str) -> bool:
    code = ord(ch)
    return unicodedata.category(ch) == "So" or 0x1F3FB <= code <= 0x1F3FF or ch in "\u200d\ufe0e\ufe0f\u20e3"


def strip_emoji(text: str) -> str:
    """The text without emoji (nor the joiners, skin tones and marks they are built from), spaces tidied on each
    line. Numbers, prices and punctuation stay."""
    kept = "".join(ch for ch in text if not _emoji_part(ch))
    return "\n".join(" ".join(line.split()) for line in kept.split("\n")).strip()


def tidy_caption(text: str, *, emojis: bool = True) -> str:
    """A caption's words, line by line, without hashtags (those are put after it) and, if asked, without emoji."""
    lines = []
    for line in str(text or "").replace("\r", "").split("\n"):
        line = HASHTAG_IN_TEXT.sub(" ", line)
        if not emojis:
            line = strip_emoji(line)
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def credit_line(attribution: str, creator: str, handle: str = "") -> str:
    """How the creator is credited when a clip is posted: your own wording when you gave one, otherwise the
    creator's @handle when it is known (tagging the account reaches the people who follow it), else their name."""
    if (attribution or "").strip():
        return attribution.strip()
    handle = (handle or "").strip().lstrip("@")
    return f"Clip from @{handle}" if handle else (f"Clip from {creator}" if creator else "")


def post_caption(text: str, credit: str, hashtags: Sequence[str]) -> str:
    """The caption to paste into TikTok (or Shorts and Reels): the words, the credit unless they already hold it,
    then the hashtags."""
    body = (text or "").strip()
    if credit and credit.lower() not in body.lower():
        body = f"{body}\n{credit}" if body else credit
    tags = " ".join(hashtags)
    return f"{body}\n\n{tags}" if tags and body else body or tags


# ------------------------------------------------------------------------------------------ thumbnail words
WHOLE_LINE = 6       # a line this short is used whole
# Filler a line may start with. Not "oh" or "wait": in "OH MY GOD" and "WAIT WHAT" they are the hook.
FILLER_START = {"um", "uh", "so", "okay", "ok", "well", "like", "yeah", "yo", "and", "but", "alright", "bro", "hey",
                "now", "then"}
# Small words and filler a thumbnail should not end on.
STOP_END = {"the", "a", "an", "to", "of", "and", "or", "as", "his", "her", "my", "your", "our", "their", "its", "in",
            "on", "at", "for", "with", "is", "was", "are", "be", "it", "that", "this", "just", "so", "but", "from", "by",
            "i", "you", "we", "they", "he", "she", "me", "him", "them", "if", "when", "than", "then", "okay", "ok",
            "yeah", "um", "uh", "well", "alright", "bro", "yo", "like", "man", "dude", "actually", "really", "pretty",
            "still"}


def _letters(word: str) -> int:
    """Letters and digits in any script (a title in Cyrillic or Arabic keeps its words)."""
    return sum(ch.isalnum() for ch in word)


def bare_word(word: str) -> str:
    """A word without the punctuation around it ($, %, apostrophes and hyphens stay)."""
    return re.sub(r"[^\w$%'-]", "", word)


def _colour_word(words: list[str]) -> str:
    """The word to colour: a price, a percentage or a number of two digits or more when there is one (the last);
    else the last real word, as a line's punch usually comes at its end; else the longest."""
    numbers = [w for w in words if re.search(r"[$%€£]", w) or len(re.findall(r"[0-9]", w)) >= 2]
    if numbers:
        return numbers[-1]
    real = [w for w in words if w.lower() not in STOP_END and _letters(w) >= 3]
    return real[-1] if real else max(reversed(words), key=_letters)


def _said_once(words: list[str]) -> list[str]:
    """A phrase said over and over counts once ("oh my God, oh my God, oh my God"); a single word may stay up to
    three times ("no no no")."""
    out: list[str] = []
    for word in words:
        out.append(word)
        low = [w.lower() for w in out]
        for n in (3, 2):
            if len(out) >= 2 * n and low[-n:] == low[-2 * n:-n]:
                del out[-n:]
                break
        else:
            if len(out) >= 4 and len(set(low[-4:])) == 1:
                out.pop()
    return out


def cover_words(line: str, max_words: int = 5) -> tuple[str, str]:
    """(words for the thumbnail, the word to colour), taken from a line of the clip: filler at the start and
    small words at the end are left out, so it reads as a punchline and ends on a real word. A line of up to six
    words is kept whole."""
    words = [bare_word(w) for w in (line or "").split()]
    words = _said_once([w for w in words if _letters(w)])
    while words and words[0].lower() in FILLER_START:
        words.pop(0)
    if len(words) > WHOLE_LINE:
        words = words[:max_words]
    while words and words[-1].lower() in STOP_END:
        words.pop()
    if not words:
        words = ["Watch", "this"]
    return " ".join(w.upper() for w in words), _colour_word(words).upper()


COVER_MAX = 40


def _drawable(text: str) -> str:
    """Letters, digits, punctuation and spaces only: the thumbnail font has no emoji or symbols."""
    return "".join(ch for ch in unicodedata.normalize("NFC", text)
                   if unicodedata.category(ch)[0] in "LNPZ" or ch in "$%&+=")


def clean_words(text: str, word: str = "") -> tuple[str, str]:
    """Words you or Claude typed, made safe for the thumbnail: one line of at most 40 characters cut at a word,
    upper case, without emoji, and a coloured word that really is one of them (else a number, or the longest)."""
    text = " ".join(_drawable(" ".join((text or "").split())).split()).upper()
    if len(text) > COVER_MAX:
        text = text[:COVER_MAX + 1].rsplit(" ", 1)[0][:COVER_MAX]
    plain = [w for w in (bare_word(w) for w in text.split()) if w]
    if not plain:
        return "", ""
    word = bare_word((word or "").upper())
    if word not in plain:
        word = _colour_word(plain)
    return text, word
