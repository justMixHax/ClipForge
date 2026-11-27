"""Claude's second look. After Claude has picked its candidates, each one is gone through again as a sceptical
editor would: does it really hold up as the kind of moment that was asked for, and would it make a stranger stop
scrolling? What does not hold up is dropped, with its reason. What does can get a better opening: the start moved
to its strongest line, and a better line to show on screen.

When "look at the picture" is on, frames of each candidate go with the request (one sheet of small frames per
candidate, closest together in its opening seconds), so Claude judges what is seen as well as what is said. That
is the only time pictures of a video leave this PC during an analysis, and it goes through the user's own Claude
plan like everything else. The request is made with no tools switched on (see claude_review.command).

A second look that cannot be taken never costs the user the picks: they stand as they were, unchecked.
"""
from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from PIL import Image

from ..editing.prompt_edit import frame_sheet
from ..services import ffmpeg
from ..transcription.sentences import Sentence
from . import claude_review, focus
from .answers import json_answers, seconds
from .claude_review import ClaudeUnavailable, Pick

FRAMES = 8                       # frames of each candidate on its sheet
TILE_WIDTH = 320                 # the width of one of them, in pixels
OPENING = (0.3, 1.2, 2.4)        # where the first three are taken, in seconds from the candidate's start
LONG_ENOUGH = 6.0                # a candidate shorter than this gets its frames evenly spread instead
EARLIER_AT_MOST = 15.0           # how far back the start may be pulled to take in a missing setup, in seconds
NEAR_A_LINE = 0.4                # a start this close to where a line starts means that line
TIMEOUT_SECONDS = 600
_SAID_AGAIN = re.compile(r"^\s*(keep|kept|drop|dropped)\s*[:\-–—]\s*", re.IGNORECASE)   # "Keep: " before a reason


@dataclass
class Verdict:
    keep: bool
    fit: float | None = None      # 0-100: how clearly it is the kind asked for
    reason: str = ""
    start: float | None = None    # where the clip should open, when that is not where it opens now
    hook: str = ""                # a better line for the screen, when there is one
    title: str = ""               # a title, for a moment that had none (see from_signals)


# ---------------------------------------------------------------------------------------------- the frames
def frame_times(start: float, end: float) -> list[float]:
    """When the frames of a candidate are taken: three in its opening seconds, the rest spread to its end."""
    length = max(0.0, end - start)
    if length < LONG_ENOUGH:
        return [round(start + length * (i + 0.5) / FRAMES, 3) for i in range(FRAMES)]
    rest = FRAMES - len(OPENING)
    first, span = start + 3.0, length - 3.3
    return ([round(start + at, 3) for at in OPENING]
            + [round(first + span * (i + 0.5) / rest, 3) for i in range(rest)])


def sheets(source: Path, picks: Sequence[Pick]) -> list[bytes]:
    """One JPEG per candidate: its frames side by side, each marked with its time in the video. Nothing at all
    when frames cannot be taken (the second look then reads the words only)."""
    made: list[bytes] = []
    try:
        with tempfile.TemporaryDirectory(prefix="clipforge_look_") as folder:
            for n, pick in enumerate(picks):
                frames, labels = [], []
                for i, at in enumerate(frame_times(pick.start, pick.end)):
                    path = ffmpeg.grab_frame(source, at, Path(folder) / f"{n}_{i}.jpg", width=TILE_WIDTH)
                    with Image.open(path) as image:
                        frames.append(image.convert("RGB"))
                    labels.append(f"{at:.1f} s")
                made.append(frame_sheet(frames, labels))
    except (ffmpeg.FFmpegError, OSError):
        return []
    return made


def from_signals(highlights: Sequence[Any], taken: Sequence[Pick], kind: focus.Focus, room: int) -> list[Pick]:
    """For a kind the picture carries: the loud, busy moments the local rules found where almost nothing is said,
    as candidates for the second look, best first, leaving out where a candidate already is. Claude never read
    them (there is little or nothing to read), so they only make sense when their frames go with the request; one
    becomes a clip only if Claude keeps it, and then with Claude's title for it."""
    found: list[Pick] = []
    for h in sorted((h for h in highlights if h.analysis_source == "signals-only"), key=lambda h: -h.score):
        if len(found) >= room:
            break
        if any(min(h.end, p.end) - max(h.start, p.start) > 1.0 for p in (*taken, *found)):
            continue
        found.append(Pick(start=h.start, end=h.end, title="", hook="", category=kind.categories[0], score=h.score,
                          reason="", local=h))
    return found


# --------------------------------------------------------------------------------------------- the request
def _moment(n: int, pick: Pick, sentences: Sequence[Sentence], loud_times: Sequence[float]) -> str:
    before = [s for s in sentences if s.end <= pick.start + 0.05 and s.start >= pick.start - EARLIER_AT_MOST][-2:]
    inside = [s for s in sentences if s.end > pick.start + 0.05 and s.start < pick.end - 0.05]
    loud = [t for t in loud_times if pick.start <= t <= pick.end]
    named = (f'"{pick.title}"' if pick.local is None else
             "found from sound and picture alone (it has no title yet)")
    rows = [f"Moment {n}: {pick.start:.1f} to {pick.end:.1f}, {named}",
            f'On-screen line now: "{pick.hook}"' if pick.hook else "On-screen line now: none"]
    if loud:
        rows.append("Loud or busy at: " + ", ".join(f"{t:.1f}" for t in loud))
    rows += [f"[{s.start:.1f}] (before the clip) {s.text}" for s in before]
    rows += [f"[{s.start:.1f}] {s.text}" for s in inside] or ["(nothing is said in it)"]
    return "\n".join(rows)


def build_prompt(sentences: Sequence[Sentence], picks: Sequence[Pick], options, *, title: str,  # noqa: ANN001
                 creator: str, game: str, loud_times: Sequence[float], pictures: bool) -> str:
    """The request for a second look at these candidates. options.focus is the kind of moment asked for; with
    pictures, one sheet of frames per candidate goes with it, in the same order."""
    kind = focus.get(getattr(options, "focus", focus.DEFAULT))
    of_a_kind = kind.key != focus.DEFAULT
    by = f" by {creator}" if creator else ""
    playing = f" (game: {game})" if game else ""
    chosen = f" as “{kind.label}” clips" if of_a_kind else ""
    wanted = (focus.brief(kind) if of_a_kind else
              "A moment holds up when it works on its own for someone who has not seen the rest of the video, and "
              "something in it is worth stopping for: it is funny, surprising, tense, impressive or interesting, "
              "with its setup and its payoff inside.")
    sent = "comes 1 picture" if len(picks) == 1 else f"come {len(picks)} pictures"
    seen = (f"\nWith this request {sent}, in the order of the moments: picture 1 belongs to "
            "moment 1, and so on. Each shows eight frames from across its moment, marked with their times; the "
            "first three are from its opening seconds. Judge what the frames show together with the lines.\n"
            if pictures else "")
    keep = ("really is this kind of moment and would make a stranger stop scrolling" if of_a_kind else
            "would make a stranger stop scrolling")
    how_clear = "it is the kind asked for" if of_a_kind else "it is worth stopping for"
    opening = f" {kind.hook}" if kind.hook else ""
    unnamed = any(pick.local is not None for pick in picks)
    title_rule = ('\n- "title": for a moment that has no title yet and that you keep, a short catchy title that names '
                  'what the frames show; otherwise "".' if unnamed else "")
    title_field = ', "title": ""' if unnamed else ""
    moments = "\n\n".join(_moment(n, pick, sentences, loud_times) for n, pick in enumerate(picks, 1))
    return f"""You are the second look in ClipForge, a tool that cuts long videos into short vertical clips for TikTok,
YouTube Shorts and Instagram Reels. A first pass chose the moments below{chosen}. Go through them as a sceptical
editor would: most clips that get posted do nothing, and a weak one wastes the post. Work only from what is given
here; do not run commands or open files.

Video: "{title}"{by}{playing}.

{wanted}
{seen}
For each moment, answer:
- "verdict": "keep" only if it {keep}; otherwise "drop".
- "fit": 0-100, how clearly {how_clear}.
- "reason": one short sentence the creator will read on the clip, naming what makes you keep or drop it.
- "start": the time of the line the clip should open on, so that its first two seconds are its strongest: one of
  the times shown with its lines (a line marked "before the clip" pulls in a setup that is missing), or its start
  as it is now.{opening}
- "hook": a better line to show on screen during those seconds (at most 12 words, never giving the payoff away),
  or "" to keep the one it has.{title_rule}
Never invent words, quotes or events: judge only what is given here.

Answer with JSON only, in this shape:
{{"checks": [{{"pick": 1, "verdict": "keep", "fit": 80, "reason": "one short sentence", "start": 251.0, "hook": ""{title_field}}}]}}
All times are in seconds from the start of the video.

{moments}"""


# ---------------------------------------------------------------------------------------------- the answer
def _verdicts(raw: object, count: int) -> dict[int, Verdict]:
    """The usable verdicts in one block of the answer, by the number of the moment (from 1). A moment whose
    verdict is neither "keep" nor "drop" gets none, so it stays as it was."""
    found: dict[int, Verdict] = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        number = claude_review._number(item.get("pick"))
        word = str(item.get("verdict") or "").strip().lower()
        if number is None or number != int(number) or not 1 <= int(number) <= count or int(number) in found:
            continue
        if word not in ("keep", "drop"):
            continue
        fit = claude_review._number(item.get("fit"))
        keep = word == "keep"
        found[int(number)] = Verdict(
            keep=keep, fit=None if fit is None else round(min(100.0, max(0.0, fit)), 1),
            reason=_reason(item.get("reason")),
            start=seconds(item.get("start")) if keep else None,
            hook=" ".join(str(item.get("hook") or "").split())[:120] if keep else "",
            title=" ".join(str(item.get("title") or "").split())[:200] if keep else "")
    return found


def _reason(raw: object) -> str:
    """The reason as one line, without the verdict said again in front of it (seen for real: "Keep: ...")."""
    text = _SAID_AGAIN.sub("", " ".join(str(raw or "").split()), count=1)
    return (text[:1].upper() + text[1:])[:300]


def parse_answer(printed: str, count: int) -> dict[int, Verdict]:
    """Read what the command-line tool printed. Raises ClaudeUnavailable when it holds no usable verdict."""
    try:
        envelope = json.loads(printed)
    except ValueError:
        raise claude_review._explain(printed.strip()[:300] or "no output") from None
    if not isinstance(envelope, dict):
        raise claude_review._explain(str(envelope)[:300])
    text = str(envelope.get("result") or "")
    if envelope.get("is_error"):
        raise claude_review._explain(text or str(envelope)[:300])
    for data in json_answers(text):  # the last block first: a correction comes after its draft
        verdicts = _verdicts(data.get("checks"), count)
        if verdicts:
            return verdicts
    raise ClaudeUnavailable("Claude gave no verdicts on its second look, so the picks stand unchecked.",
                            hint=text[:300])


def ask(prompt: str, count: int, *, model: str = "sonnet", pictures: Sequence[bytes] = (),
        should_cancel: Callable[[], bool] | None = None, timeout: float = TIMEOUT_SECONDS) -> dict[int, Verdict]:
    """Take the second look: the verdicts on `count` moments, by their number."""
    return parse_answer(claude_review.run_claude(prompt, model=model, should_cancel=should_cancel, timeout=timeout,
                                                 pictures=pictures), count)


# ------------------------------------------------------------------------------------- what comes of it
def better_start(pick: Pick, wanted: float | None, sentences: Sequence[Sentence], options) -> float:  # noqa: ANN001
    """Where the clip starts after the second look. A start is only moved onto the start of a line: later, to a
    line within its opening third that leaves the clip long enough; or earlier, by a little, to take in a setup,
    while the clip stays within its longest length. Anything else leaves the start where it was."""
    if wanted is None or abs(wanted - pick.start) < 0.05 or not sentences:
        return pick.start
    line = min(sentences, key=lambda s: abs(s.start - wanted))
    opening = next((s for s in sentences if s.end > pick.start + 0.05 and s.start < pick.end - 0.05), None)
    if abs(line.start - wanted) > NEAR_A_LINE or line is opening:   # the line it opens on anyway is no move
        return pick.start
    start, length = line.start, pick.end - pick.start
    if start > pick.start:
        if start - pick.start > length / 3 + 0.01 or pick.end - start < options.min_len:
            return pick.start
    elif pick.start - start > EARLIER_AT_MOST or pick.end - start > options.max_len:
        return pick.start
    return round(start, 3)


def apply(picks: Sequence[Pick], verdicts: dict[int, Verdict], sentences: Sequence[Sentence], options, *,  # noqa: ANN001
          looked: bool) -> tuple[list[Pick], list[tuple[Pick, str]]]:
    """The candidates after the second look: those kept (with both scores, and their better opening), and those
    dropped with the reason. A candidate without a verdict stays exactly as it was, unless Claude never read it
    (a moment found from sound and picture): that one only becomes a clip when Claude has seen it and kept it."""
    kept: list[Pick] = []
    dropped: list[tuple[Pick, str]] = []
    for n, pick in enumerate(picks, 1):
        verdict = verdicts.get(n)
        if verdict is None:
            if pick.local is None:
                kept.append(pick)
            continue
        if not verdict.keep:
            dropped.append((pick, verdict.reason or "It does not hold up."))
            continue
        unread = pick.local is not None
        if unread:                       # Claude is the one who saw it: its title and its reason are the clip's
            pick = replace(pick, title=verdict.title or pick.local.title, reason=verdict.reason)
        start = better_start(pick, verdict.start, sentences, options)
        hook, hooks = pick.hook, list(pick.hooks)
        if verdict.hook and verdict.hook.lower() != pick.hook.lower():
            hook = verdict.hook
            hooks = [line for line in dict.fromkeys([pick.hook, *pick.hooks]) if line and line.lower() != hook.lower()]
        score = pick.score if verdict.fit is None else round((pick.score + verdict.fit) / 2, 1)
        checked = {"fit": verdict.fit, "reason": verdict.reason, "looked": looked, "moved": round(start - pick.start, 2)}
        if unread:                       # its reason is the clip's own first line already: not said twice
            checked.update(reason="", unread=True)
        kept.append(replace(pick, start=start, hook=hook, hooks=hooks, score=score, checked=checked))
    return kept, dropped
