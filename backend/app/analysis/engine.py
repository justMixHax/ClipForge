"""Highlight discovery: from a transcript plus audio and visual signals to ranked, explained suggestions.

With speech: every run of whole sentences inside the requested length range is scored, then the best
distinct ones are kept. Wherever people are talking, the words decide.

Where almost nothing is said (gameplay between bits of commentary, a film's action scenes, a video with
no speech at all), moments are found from sound and picture peaks instead. They only fill places the
spoken moments left empty, they never cut a sentence in half, and they are clearly marked as resting on
sound and picture alone, with low confidence.
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

from ..errors import JobCancelled
from ..transcription.sentences import Sentence, Word
from . import focus
from .audio import AudioFeatures, _smooth
from .candidates import WindowOptions, enumerate_windows
from .dedupe import ExistingClip, select_distinct
from .describe import build_description, build_hashtags, build_hooks, build_title, merge_hashtags
from .feedback import FeedbackProfile
from .presets import DIMENSIONS, effective_weights
from .scoring import ScoreOptions, ScoringContext, _clock
from .visual import VisualFeatures

def _plain(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


MIN_WORDS_FOR_SPEECH = 20   # below this the video is treated as having no usable speech
ACTION_STEP = 0.5           # seconds per sample of the action curve
MAX_TALK_IN_ACTION = 0.25   # an action moment may be at most this much talk; beyond that the words decide
SHORT_REMARK = 4.0          # a sentence this short at the edge of an action moment is kept whole inside it
TALK_DENSE = 0.5            # where talk fills more than this share of the surroundings, action is not looked for


@dataclass
class HighlightOptions:
    count: int = 5
    min_len: float = 15.0
    max_len: float = 60.0
    min_score: float = 0.0
    preset: str = "auto"
    game_style: str = ""
    niche_keywords: tuple[str, ...] = ()
    language: str = "en"
    focus: str = focus.DEFAULT   # the kind of moment asked for (see focus.py)
    pre_roll: float = 0.15
    post_roll: float = 0.35


@dataclass
class Highlight:
    start: float
    end: float
    score: float
    subscores: dict[str, float]
    confidence: float
    explanation: str
    evidence: list[dict[str, Any]]
    category: str
    title: str
    hook_text: str
    hook_options: list[str]
    hashtags: list[str]
    description: str
    transcript_text: str
    rank: int
    analysis_source: str
    peak_time: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


def find_highlights(words: list[Word], sentences: list[Sentence], audio: AudioFeatures | None,
                    visual: VisualFeatures | None, options: HighlightOptions, *, duration: float | None = None,
                    existing: Sequence[ExistingClip] = (), feedback: FeedbackProfile | None = None,
                    video_title: str = "", creator: str = "",
                    should_cancel: Callable[[], bool] | None = None,
                    on_progress: Callable[[float], None] | None = None) -> list[Highlight]:
    if duration is None:
        duration = max(audio.duration if audio else 0.0, visual.duration if visual else 0.0,
                       words[-1].end if words else 0.0)

    def check() -> None:
        if should_cancel is not None and should_cancel():
            raise JobCancelled()

    check()
    kind = focus.get(options.focus)
    spoken = sum(1 for w in words if w.text.strip())
    if not sentences or spoken < MIN_WORDS_FOR_SPEECH:
        return _noted(_action_highlights(audio, visual, options, duration, existing, feedback, creator, video_title,
                                         count=options.count, words=words, sentences=sentences), kind)
    # A kind that the picture carries (gameplay, scares): the loud, busy moments are looked at first, and what
    # is said fills the places they leave.
    first = (_action_highlights(audio, visual, options, duration, existing, feedback, creator, video_title,
                                count=options.count, words=words, sentences=sentences)
             if kind.led_by == "picture" else [])

    ctx = ScoringContext(words, sentences, audio, visual,
                         ScoreOptions(preset=options.preset, feedback=feedback,
                                      niche_keywords=tuple(options.niche_keywords), game_style=options.game_style,
                                      language=options.language, pre_roll=options.pre_roll,
                                      post_roll=options.post_roll), duration)
    window_opts = WindowOptions(options.min_len, options.max_len, options.pre_roll, options.post_roll)
    windows = list(enumerate_windows(sentences, window_opts))
    if not windows:
        # Everything that was said is shorter than the minimum length: offer it as a single clip.
        total = sentences[-1].end - sentences[0].start
        if 5.0 <= total <= options.max_len:
            windows = [(0, len(sentences) - 1)]

    scored = []
    for n, (i, j) in enumerate(windows):
        if n % 200 == 0:
            check()
            if on_progress is not None:
                on_progress(0.9 * n / len(windows))
        scored.append(ctx.score(i, j))
    if kind.key != focus.DEFAULT:
        scored = [replace(c, score=focus.local_score(c.score, c.category, kind)) for c in scored]

    taken_first = [ExistingClip(h.start, h.end, frozenset()) for h in first]
    picked = select_distinct(scored, options.count - len(first), existing=[*existing, *taken_first],
                             min_score=options.min_score, uniqueness_weight=ctx.weights["uniqueness"])
    highlights: list[Highlight] = []
    for rank, cand in enumerate(picked, 1):
        check()
        full = ctx.score(cand.i, cand.j, explain=True)
        uniqueness = cand.subscores["uniqueness"]
        full.subscores["uniqueness"] = uniqueness
        if uniqueness < 85:
            full.evidence.append({"dimension": "uniqueness", "impact": round(uniqueness - 100, 1), "time": None,
                                  "quote": None,
                                  "text": "Shares part of its wording with a higher-ranked clip from this video"})
        if kind.key != focus.DEFAULT:
            fits = full.category in kind.categories
            full.evidence.append({
                "dimension": "focus", "impact": focus.LOCAL_BONUS if fits else -focus.LOCAL_PENALTY, "time": None,
                "quote": None,
                "text": f"Asked for “{kind.label}”: the local rules "
                        + ("read this as one" if fits else "do not read this as one")})
        text = " ".join(words[k].text for k in range(full.first_word, full.last_word + 1))
        title = build_title(ctx, full)
        hooks = build_hooks(ctx, full)
        # Default on-screen hook: the line the clip is titled after, rather than whatever happens to be said
        # first (which the viewer is about to hear anyway). The others stay available as alternatives.
        title_key = _plain(title)
        hooks.sort(key=lambda hook: 0 if _plain(hook) and (_plain(hook) in title_key or title_key in _plain(hook)) else 1)
        highlights.append(Highlight(
            start=full.start, end=full.end, score=cand.score, subscores=full.subscores, confidence=full.confidence,
            explanation=full.explanation, evidence=full.evidence, category=full.category, title=title,
            hook_text=hooks[0], hook_options=hooks,
            hashtags=build_hashtags(full.category, options.preset, options.game_style, creator,
                                    f"{video_title} {text}"),
            description=build_description(title, creator), transcript_text=text, rank=rank,
            analysis_source="heuristic", peak_time=full.peak_time))

    action = list(first)
    if len(highlights) + len(action) < options.count:
        # Places still free: offer the loud, busy moments from the stretches where almost nothing is said.
        check()
        action += _action_highlights(
            audio, visual, options, duration, existing, feedback, creator, video_title,
            count=options.count - len(highlights) - len(action), words=words, sentences=sentences,
            taken=[(h.start, h.end) for h in [*highlights, *action]])
    if action:
        highlights += _noted(action, kind)
        highlights.sort(key=lambda h: -h.score)
        for rank, h in enumerate(highlights, 1):
            h.rank = rank
    if on_progress is not None:
        on_progress(1.0)
    return highlights


# ---------------------------------------------------------------------------------------- no-speech path
def _noted(action: list[Highlight], kind: focus.Focus) -> list[Highlight]:
    """Moments found from sound and picture, each saying what that means for the kind of moment asked for."""
    if kind.key != focus.DEFAULT:
        for h in action:
            h.evidence.append({
                "dimension": "focus", "impact": 0.0, "time": None, "quote": None,
                "text": f"Asked for “{kind.label}”: " + (
                    "loud, busy moments like this one are looked at first" if kind.led_by == "picture" else
                    "found from sound and picture only, so the local rules cannot tell whether it is one")})
    return action


def _resample_max(values: np.ndarray, src_hz: float, n_out: int) -> np.ndarray:
    """Down-sample by taking the maximum of each block, so short peaks are not averaged away."""
    block = max(1, int(round(src_hz * ACTION_STEP)))
    usable = (values.size // block) * block
    if usable == 0:
        return np.zeros(n_out, dtype=np.float32)
    out = values[:usable].reshape(-1, block).max(axis=1)
    if out.size < n_out:
        out = np.pad(out, (0, n_out - out.size))
    return out[:n_out].astype(np.float32)


def _fit_action_window(peak_t: float, length: float, duration: float, options: HighlightOptions,
                       sentences: Sequence[Sentence],
                       blocked: Sequence[tuple[float, float]]) -> tuple[float, float] | None:
    """A clip around a peak of action, or None when no acceptable one exists there.

    Sentences are kept whole or left out, never cut: a short remark at an edge, and whatever is being said
    at the peak itself, is taken in; a longer sentence at an edge is left out. The result must fit the
    requested length, be mostly free of talk, and not overlap a clip that already exists."""
    start = min(max(0.0, peak_t - 0.4 * length), max(0.0, duration - length))
    end = min(duration, start + length)
    for _ in range(6):
        moved = False
        for sentence in sentences:
            if sentence.end <= start or sentence.start >= end:
                continue
            cut_at_start = sentence.start < start - 1e-6
            cut_at_end = sentence.end > end + 1e-6
            if not (cut_at_start or cut_at_end):
                continue
            keep = sentence.end - sentence.start <= SHORT_REMARK or sentence.start <= peak_t <= sentence.end
            if cut_at_start:
                start = max(0.0, sentence.start - options.pre_roll) if keep else sentence.end + 0.1
            if cut_at_end:
                end = min(duration, sentence.end + options.post_roll) if keep else sentence.start - 0.1
            moved = True
        if not moved:
            break
    else:
        return None  # the edges would not settle: too much talk around here
    if not (start <= peak_t <= end) or not (options.min_len - 0.5 <= end - start <= options.max_len + 0.5):
        return None
    talk = sum(max(0.0, min(end, sentence.end) - max(start, sentence.start)) for sentence in sentences)
    if talk > MAX_TALK_IN_ACTION * (end - start):
        return None
    for other_start, other_end in blocked:
        shared = min(end, other_end) - max(start, other_start)
        if shared > 1.0 or shared > 0.1 * min(end - start, other_end - other_start):
            return None
    return round(start, 3), round(end, 3)


def _action_highlights(audio: AudioFeatures | None, visual: VisualFeatures | None, options: HighlightOptions,
                       duration: float, existing: Sequence[ExistingClip], feedback: FeedbackProfile | None,
                       creator: str, video_title: str, *, count: int | None = None,
                       words: Sequence[Word] = (), sentences: Sequence[Sentence] = (),
                       taken: Sequence[tuple[float, float]] = ()) -> list[Highlight]:
    """Up to `count` moments found from loudness and picture activity, in stretches with almost no talk.
    `taken` are clips already chosen from speech in this run; nothing here may overlap them."""
    count = options.count if count is None else count
    n = int(duration / ACTION_STEP)
    if n < 4 or count <= 0:
        return []
    loud = np.zeros(n, dtype=np.float32)
    activity = np.zeros(n, dtype=np.float32)
    if audio is not None and audio.rms_db.size:
        active = audio.rms_db > audio.silence_db
        loud = _resample_max(np.where(active, np.clip(_smooth(audio.delta_db), 0, 18), 0.0), 1 / audio.hop, n)
    if visual is not None and visual.motion.size:
        ratio = np.clip(visual.motion / max(visual.motion_ref, 0.02), 0.0, 1.0)
        grid = np.interp(np.arange(n) * ACTION_STEP, np.arange(ratio.size) / visual.fps, ratio)
        activity = np.clip(grid - float(np.median(grid)), 0.0, 1.0).astype(np.float32)
    curve = _smooth(loud / 12.0 + activity, width=6)
    threshold = max(0.6, float(curve.mean() + curve.std()))

    length = min(options.max_len, max(options.min_len, (options.min_len + options.max_len) / 2))
    length = min(length, duration)
    weights = effective_weights(options.preset, feedback)
    work = curve.copy()
    blocked = [(e.start, e.end) for e in existing] + [(float(a), float(b)) for a, b in taken]
    for a, b in taken:  # moments already chosen from speech keep their own peaks
        work[max(0, int(a / ACTION_STEP)):int(math.ceil(b / ACTION_STEP)) + 1] = 0.0
    if sentences:
        # Do not even look where people are mostly talking: there the words have already decided.
        talking = np.zeros(n, dtype=np.float32)
        for sentence in sentences:
            talking[max(0, int(sentence.start / ACTION_STEP)):int(math.ceil(sentence.end / ACTION_STEP))] = 1.0
        span = max(1, int(options.min_len / ACTION_STEP))
        work[np.convolve(talking, np.ones(span, dtype=np.float32) / span, mode="same") > TALK_DENSE] = 0.0

    found: list[Highlight] = []
    near = max(1, int(2.0 / ACTION_STEP))
    for _ in range(400):
        if len(found) >= count:
            break
        k = int(np.argmax(work))
        if work[k] < threshold:
            break
        peak_t = k * ACTION_STEP
        window = _fit_action_window(peak_t, length, duration, options, sentences, blocked)
        if window is None:
            work[max(0, k - near):k + near + 1] = 0.0  # not here; a peak a little further on may still do
            continue
        start, end = window
        a, b = int(start / ACTION_STEP), min(n, int(math.ceil(end / ACTION_STEP)))
        guard = int(0.2 * (end - start) / ACTION_STEP)
        work[max(0, a - guard):b + guard] = 0.0

        loud_db = float(loud[a:b].max()) if b > a else 0.0
        busy = float(activity[a:b].max()) if b > a else 0.0
        position = (peak_t - start) / (end - start)
        subscores = {
            "hook": 40.0, "entertainment": 30 + min(30.0, 2.2 * loud_db) + 25 * busy,
            "emotion": 20 + min(40.0, 3 * loud_db), "clarity": 50.0, "visual": 34 + 46 * busy if visual else 50.0,
            "pacing": 55.0, "payoff": 45 + (10 if 0.3 <= position <= 0.8 else 0), "shareability": 40.0,
            "relevance": 50.0, "subtitles": 50.0, "originality": 60.0, "uniqueness": 100.0,
        }
        subscores = {dim: round(min(100.0, max(0.0, v)), 1) for dim, v in subscores.items()}
        score = round(sum(weights[d] * subscores[d] for d in DIMENSIONS), 1)
        if score < options.min_score:
            continue
        said = " ".join(w.text for w in words if w.start >= start - 0.05 and w.end <= end + 0.05 and w.text.strip())
        evidence = [{"dimension": "clarity", "impact": -10.0, "time": None, "quote": None,
                     "text": ("Almost nothing is said here" if said else "No speech was found here")
                             + ", so this suggestion rests on sound and picture only"}]
        if loud_db >= 4:
            evidence.append({"dimension": "emotion", "impact": round(min(40.0, 3 * loud_db), 1), "quote": None,
                             "time": round(peak_t, 2),
                             "text": f"Sound peaks {loud_db:.0f} dB above the surrounding level at {_clock(peak_t)}"})
        if busy >= 0.2:
            evidence.append({"dimension": "visual", "impact": round(46 * busy, 1), "quote": None,
                             "time": round(peak_t, 2), "text": "The picture is much busier here than in the rest "
                                                               "of the video"})
        title = f"Action at {_clock(peak_t)}"
        found.append(Highlight(
            start=start, end=end, score=score, subscores=subscores,
            confidence=round(min(0.45, 0.30 + (0.08 if loud_db >= 6 and busy >= 0.2 else 0.0)), 2),
            explanation=". ".join(e["text"] for e in evidence[1:] + evidence[:1]) + ".", evidence=evidence,
            category="action", title=title, hook_text="", hook_options=[],
            hashtags=build_hashtags("action", options.preset, options.game_style, creator, video_title),
            description=build_description(title, creator), transcript_text=said, rank=0,
            analysis_source="signals-only", peak_time=round(peak_t, 2)))
        blocked.append((start, end))
    found.sort(key=lambda h: -h.score)
    for rank, h in enumerate(found, 1):
        h.rank = rank
    return found


# ----------------------------------------------------------------------------------------- Claude's picks
def _second_look(checked: dict[str, Any]) -> dict[str, Any]:
    """What Claude's second look said about a pick it kept, as a line on the clip."""
    looked = bool(checked.get("looked"))
    text = ("Second look, with frames of the video: " if looked else "Second look: ") + (
        "found from sound and picture, and kept once Claude had seen it." if checked.get("unread") else
        str(checked.get("reason") or "").strip() or "it holds up.")
    moved = float(checked.get("moved") or 0.0)
    if abs(moved) >= 0.05:
        text = (text.rstrip(". ") + f". It now opens {abs(moved):.1f} s {'later' if moved > 0 else 'earlier'}, "
                "on its strongest line.")
    return {"dimension": "check", "impact": 0.0, "time": None, "quote": None, "text": text, "verdict": "keep",
            "fit": checked.get("fit"), "looked": looked}


def _cover(pick: Any) -> dict[str, str]:
    """The thumbnail words an AI wrote for its pick, if it did."""
    return {"cover_text": pick.thumb, "cover_word": pick.thumb_word} if getattr(pick, "thumb", "") else {}


def highlights_from_picks(words: list[Word], sentences: list[Sentence], audio: AudioFeatures | None,
                          visual: VisualFeatures | None, options: HighlightOptions, picks: Sequence[Any], *,
                          duration: float, existing: Sequence[ExistingClip] = (),
                          feedback: FeedbackProfile | None = None, video_title: str = "",
                          creator: str = "", source: str = "claude") -> list[Highlight]:
    """Turn the moments an AI picked (source "claude", or "chatgpt" for its additions) into highlights. Each pick is widened to whole sentences, keeps the
    12-part breakdown from the local rules for reference, and takes Claude's score, title and reason."""
    ctx = None
    if sentences:
        ctx = ScoringContext(words, sentences, audio, visual,
                             ScoreOptions(preset=options.preset, feedback=feedback,
                                          niche_keywords=tuple(options.niche_keywords),
                                          game_style=options.game_style, language=options.language,
                                          pre_roll=options.pre_roll, post_roll=options.post_roll), duration)
    kind = focus.get(options.focus)
    taken = [(e.start, e.end) for e in existing]
    found: list[Highlight] = []
    for pick in sorted(picks, key=lambda p: -p.score):
        if len(found) >= options.count or pick.score < options.min_score:
            continue
        start, end = max(0.0, pick.start), min(duration, pick.end)
        inside = [s.idx for s in sentences if s.end > start + 0.05 and s.start < end - 0.05]
        if source == "chatgpt" and not inside:
            continue  # ChatGPT only reads the transcript: a moment where nobody speaks is not one it heard
        # A moment the local rules found from sound and picture, which Claude saw and kept: it keeps its own
        # stretch and what was measured there (a remark inside it must not shrink the clip to that remark).
        local = getattr(pick, "local", None)
        full = (ctx.score(inside[0], inside[-1], explain=True)
                if (ctx is not None and inside and local is None) else None)
        if full is not None:
            start, end = full.start, full.end
        if not (max(3.0, 0.8 * options.min_len) <= end - start <= options.max_len + 10):
            continue  # outside the length asked for (a little leeway for whole sentences)
        if any(min(end, b) - max(start, a) > min(1.0, 0.1 * (end - start)) for a, b in taken):
            continue
        taken.append((start, end))
        said = " ".join(w.text for w in words if w.start >= start - 0.05 and w.end <= end + 0.05 and w.text.strip())
        who = "ChatGPT" if source == "chatgpt" else "Claude"
        reason = pick.reason or f"{who} picked this moment."
        evidence = [{"dimension": "entertainment", "impact": 0.0, "time": None, "quote": None,
                     "text": f"{who}: {reason}"}]
        if getattr(pick, "checked", None):
            evidence.append(_second_look(pick.checked))
        proof, fit = getattr(pick, "proof", ""), getattr(pick, "fit", None)
        if kind.key != focus.DEFAULT and (proof or fit is not None):
            evidence.append({"dimension": "focus", "impact": 0.0, "time": None, "quote": proof or None, "fit": fit,
                             "text": f"Asked for “{kind.label}”: what makes this one"})
        if full is not None:
            evidence += full.evidence
        elif local is not None:   # what was measured there; what the local rules guessed about the kind is not kept
            evidence += [e for e in local.evidence if e.get("dimension") != "focus"]
        subscores = (dict(full.subscores) if full is not None else dict(local.subscores) if local is not None
                     else {d: 50.0 for d in DIMENSIONS})
        title = pick.title or (said[:60] if said else f"Moment at {_clock(start)}")
        hook = pick.hook or title
        hooks = [line for line in dict.fromkeys([hook, *getattr(pick, "hooks", []), title]) if line]
        found.append(Highlight(
            start=round(start, 3), end=round(end, 3), score=pick.score, subscores=subscores,
            confidence=0.8 if source == "claude" else 0.7,
            explanation=f"{'Added by ChatGPT' if source == 'chatgpt' else 'Picked by Claude'}: {reason}",
            evidence=evidence, category=pick.category,
            title=title, hook_text=hook, hook_options=hooks,
            hashtags=merge_hashtags(pick.hashtags, build_hashtags(pick.category, options.preset, options.game_style,
                                                                  creator, f"{video_title} {said}")),
            description=build_description(pick.caption or title, creator), transcript_text=said, rank=0,
            analysis_source=source,
            peak_time=round(full.peak_time if full is not None else local.peak_time if local is not None else start, 2),
            extra=_cover(pick)))
    found.sort(key=lambda h: -h.score)
    for rank, h in enumerate(found, 1):
        h.rank = rank
    return found


def apply_second_opinion(picks: list[Highlight], review: Any, words: list[Word], sentences: list[Sentence],
                         audio: AudioFeatures | None, visual: VisualFeatures | None, options: HighlightOptions, *,
                         duration: float, existing: Sequence[ExistingClip] = (),
                         feedback: FeedbackProfile | None = None, video_title: str = "",
                         creator: str = "") -> tuple[list[Highlight], dict[str, list]]:
    """Record ChatGPT's verdict on each pick and add the missed moments it found. Nothing is dropped: a clip
    ChatGPT disagrees with stays, marked, so the user decides. Returns (all highlights, summary)."""
    from .chatgpt_review import MAX_ADDITIONS

    summary: dict[str, list] = {"agreed": [], "disagreed": [], "added": []}
    for number, highlight in enumerate(picks, 1):
        if number not in review.verdicts:
            continue
        verdict, reason = review.verdicts[number]
        highlight.evidence.append({
            "dimension": "chatgpt", "verdict": verdict, "impact": 0.0, "time": None, "quote": None,
            "text": f"ChatGPT {'agrees' if verdict == 'agree' else 'disagrees'}" + (f": {reason}" if reason else ".")})
        if verdict == "agree":
            summary["agreed"].append(highlight.title)
        else:
            summary["disagreed"].append((highlight.title, reason))
    added: list[Highlight] = []
    if review.additions:  # scoring setup is expensive on long videos: only when there is something to add
        taken = list(existing) + [ExistingClip(h.start, h.end, frozenset()) for h in picks]
        extra_options = HighlightOptions(**{**options.__dict__, "count": MAX_ADDITIONS})
        added = highlights_from_picks(words, sentences, audio, visual, extra_options, review.additions,
                                      duration=duration, existing=taken, feedback=feedback, video_title=video_title,
                                      creator=creator, source="chatgpt")
    summary["added"] = [h.title for h in added]
    merged = sorted([*picks, *added], key=lambda h: -h.score)
    for rank, highlight in enumerate(merged, 1):
        highlight.rank = rank
    return merged, summary



# ------------------------------------------------------------------------------------------ your own moments
WORD_EDGE = 0.6  # the most ClipForge moves your start or end, to keep a word whole
MIN_MOMENT = 1.0  # the shortest clip a moment may become after that


def _word_at(t: float, words: Sequence[Word]) -> Word | None:
    """The word a cut at t would chop (same margins as the quality check), or None."""
    return next((w for w in words if w.start + 0.06 < t < w.end - 0.06), None)


def _covering(sentences: Sequence[Sentence], start: float, end: float) -> list[int]:
    return [s.idx for s in sentences if s.end > start + 0.05 and s.start < end - 0.05]


def moment_end(start: float, sentences: Sequence[Sentence], ctx: ScoringContext | None, *, min_len: float,
               max_len: float, duration: float, post_roll: float = 0.25) -> float:
    """Where a moment you started by hand ends when you did not say: at the end of a sentence inside your
    length range, the one the local rules score highest (closest to the middle of the range on a tie), never
    past the end of the video. Without any sentence ending there, the middle of the range."""
    latest = min(duration, start + max_len)
    target = start + (min_len + max_len) / 2
    first = next((s.idx for s in sentences if s.end > start + 0.05), None)
    candidates = [s for s in sentences if start + min_len <= s.end <= latest]
    if not candidates or first is None:
        return round(max(start + 0.5, min(latest, target)), 3)

    def rank(s: Sentence) -> tuple[float, float]:
        score = ctx.score(first, s.idx).score if ctx is not None and s.idx >= first else 0.0
        return (-score, abs(s.end - target))

    best = min(candidates, key=rank)
    following = [s.start for s in sentences if s.idx > best.idx]
    end = best.end + post_roll
    if following:
        end = min(end, max(best.end, following[0] - 0.05))
    return round(min(end, latest), 3)


def moment_highlight(words: list[Word], sentences: list[Sentence], audio: AudioFeatures | None,
                     visual: VisualFeatures | None, options: HighlightOptions, *, start: float, end: float | None,
                     pick: Any = None, duration: float, feedback: FeedbackProfile | None = None,
                     video_title: str = "", creator: str = "") -> Highlight:
    """A moment you picked, given the same treatment as one the engine found: the local 12-part breakdown and
    evidence, a title, hook lines and hashtags. Your start is kept exactly. Without an end from you, the end is
    Claude's (pick) when it fits your length, otherwise the one moment_end chooses. Claude's title, hook,
    category and score are used when it gave them."""
    ctx = None
    if sentences:
        ctx = ScoringContext(words, sentences, audio, visual,
                             ScoreOptions(preset=options.preset, feedback=feedback,
                                          niche_keywords=tuple(options.niche_keywords),
                                          game_style=options.game_style, language=options.language,
                                          pre_roll=options.pre_roll, post_roll=options.post_roll), duration)
    moved: dict[str, dict[str, Any]] = {}
    end_from = "you"
    if end is None and pick is not None and options.min_len <= pick.end - start <= options.max_len \
            and pick.end <= duration + 0.05:
        end, end_from = min(duration, float(pick.end)), "claude"
    if end is None:
        end, end_from = moment_end(start, sentences, ctx, min_len=options.min_len, max_len=options.max_len,
                                   duration=duration), "forge"
    chopped = _word_at(end, words)
    if chopped is not None and chopped.end - end <= WORD_EDGE:
        following = next((w.start for w in words if w.start >= chopped.end), None)
        finish = chopped.end + 0.05 if following is None else min(chopped.end + 0.05, max(chopped.end, following - 0.02))
        moved["end"] = {"by": round(finish - end, 2), "word": chopped.text}
        end = finish
    end = round(min(duration, end), 3)
    chopped = _word_at(start, words)
    if chopped is not None and chopped.end - start <= WORD_EDGE and end - (chopped.end + 0.02) >= MIN_MOMENT:
        # typed times are whole seconds: keep the first word whole, unless that would leave too little clip
        moved["start"] = {"by": round(chopped.end + 0.02 - start, 2), "word": chopped.text}
        start = chopped.end + 0.02

    inside = _covering(sentences, start, end)
    full = ctx.score(inside[0], inside[-1], explain=True) if (ctx is not None and inside) else None
    said = " ".join(w.text for w in words if w.start >= start - 0.05 and w.end <= end + 0.05 and w.text.strip())
    evidence: list[dict[str, Any]] = [{"dimension": "entertainment", "impact": 0.0, "time": round(start, 2),
                                       "quote": None, "text": "You picked this moment yourself."}]
    caption, own_tags, cover = "", [], {}
    if pick is not None:
        caption, own_tags, cover = pick.caption, pick.hashtags, _cover(pick)
        title = pick.title or (said[:60] if said else f"Moment at {_clock(start)}")
        hook = pick.hook or title
        hooks = [hook] + ([title] if title != hook else [])
        category, score = pick.category, float(pick.score)
        reason = f"Claude: {pick.reason}" if pick.reason else "Claude named it."
        evidence.append({"dimension": "entertainment", "impact": 0.0, "time": None, "quote": None, "text": reason})
        confidence = 0.8
    elif full is not None:
        title = build_title(ctx, full)
        hooks = build_hooks(ctx, full)
        hook, category, score = hooks[0], full.category, full.score
        reason, confidence = full.explanation, full.confidence
    else:
        title, hooks, hook = f"Moment at {_clock(start)}", [], ""
        category, score, confidence = "action", 50.0, 0.3
        reason = "Nothing is said here, so there is no automatic score: it rests on your choice."
    if full is not None:
        evidence += full.evidence
    return Highlight(
        start=round(start, 3), end=end, score=round(score, 1),
        subscores=dict(full.subscores) if full is not None else {d: 50.0 for d in DIMENSIONS},
        confidence=confidence, explanation=f"You picked this moment. {reason}".strip(), evidence=evidence,
        category=category, title=title, hook_text=hook, hook_options=hooks,
        hashtags=merge_hashtags(own_tags, build_hashtags(category, options.preset, options.game_style, creator,
                                                         f"{video_title} {said}")),
        description=build_description(caption or title, creator), transcript_text=said, rank=0,
        analysis_source="manual", peak_time=round(full.peak_time if full is not None else start, 2),
        extra={"end_from": end_from, "moved": moved, **cover})
