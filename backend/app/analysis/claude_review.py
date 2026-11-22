"""Claude picks the clips: the transcript goes to Claude, which chooses the moments worth clipping and
says why. This is ClipForge's main engine when it is switched on in Settings (the default).

ClipForge talks to Claude through the Claude Code command-line tool installed on this PC, so it uses the
user's own Claude plan and sign-in. No API key is stored or needed. The tool runs with no tools of its own
(it can only read the prompt and answer), from an empty folder outside the project, and its answer is
checked before anything is used.

When Claude cannot be reached (not installed, signed out, plan limit reached), ClipForge falls back to its
local scoring rules and says so on the job, so an analysis never fails because of it.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..errors import ClipForgeError, JobCancelled
from ..transcription.sentences import Sentence
from .scoring import CATEGORY_LABELS

MODELS = ("sonnet", "opus", "haiku")
TIMEOUT_SECONDS = 900
CANDIDATE_PATHS = (Path.home() / ".local" / "bin" / "claude.exe", Path.home() / ".local" / "bin" / "claude")
SIGN_IN_HINT = ("Open PowerShell, run  claude auth login  and sign in with your Claude account, then analyse "
                "the video again.")


class ClaudeUnavailable(ClipForgeError):
    """Claude could not give an answer. The message says why in plain words."""


@dataclass
class Pick:
    start: float
    end: float
    title: str
    hook: str
    category: str
    score: float
    reason: str


def clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def find_cli(configured: str = "") -> str | None:
    """Where the Claude Code command-line tool is, or None."""
    if configured:
        return configured if Path(configured).is_file() else None
    found = shutil.which("claude")
    if found:
        return found
    return next((str(p) for p in CANDIDATE_PATHS if p.is_file()), None)


def child_env() -> dict[str, str]:
    """The environment the tool runs with: this PC's, minus any Claude or Anthropic settings inherited from the
    program ClipForge happened to be started from (the desktop app, an editor), so the tool uses your own
    sign-in. Where that sign-in is kept (CLAUDE_CONFIG_DIR) is kept."""
    return {k: v for k, v in os.environ.items()
            if k == "CLAUDE_CONFIG_DIR" or not k.upper().startswith(("ANTHROPIC_", "CLAUDE", "CLIPFORGE_"))}


def command(cli: str, model: str) -> list[str]:
    # --bare is avoided on purpose: that mode only accepts an API key, not the plan's own sign-in.
    return [cli, "-p", "--output-format", "json", "--model", model, "--tools", "",
            "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"]


# ------------------------------------------------------------------------------------------- the prompt
def build_prompt(sentences: Sequence[Sentence], options, *, title: str, creator: str, game: str,  # noqa: ANN001
                 action_times: Sequence[float], duration: float) -> str:
    lines = [f"[{clock(s.start)}] {s.text}" for s in sentences]
    action = ", ".join(clock(t) for t in action_times[:40]) or "none found"
    by = f" by {creator}" if creator else ""
    playing = f" (game: {game})" if game else ""
    return f"""You are the clip picker of ClipForge, a tool that cuts long videos into short vertical clips for
TikTok, YouTube Shorts and Instagram Reels.

Video: "{title}"{by}{playing}, {clock(duration)} long.

Pick the {options.count} best moments to post as clips (fewer if there are not that many good ones). Each clip
must be between {options.min_len:g} and {options.max_len:g} seconds long.

What makes a good clip:
- It works on its own for someone who has not seen the rest of the video.
- It is genuinely funny, surprising, tense, impressive or interesting: deadpan banter, comebacks, callbacks,
  fake names, absurd situations, a clear story with a punchline, a fight or a big moment.
- It starts at the start of a line (with any setup the joke needs) and ends right after the punchline or
  payoff, not in the middle of a sentence.
- Loud does not mean good. Shouting during a struggle is only worth it if something happens.
Moments where little is said but a lot happens are listed here as loud or busy: {action}. Use them only if the
lines around them suggest something worth seeing.

Rules: use only times from the transcript. Never invent words, quotes or events: titles and hooks must reflect
what is really said. Score each pick 0-100 for how likely it is to work as a short, honestly; it is an
estimate, not a promise. Category is one of: {', '.join(CATEGORY_LABELS)}.

Answer with JSON only, in this shape:
{{"picks": [{{"start": 251.0, "end": 301.5, "title": "short catchy title", "hook": "on-screen line, max 12 words",
"category": "funny", "score": 85, "reason": "one sentence on why this works"}}]}}
Times are in seconds from the start of the video.

Transcript, one line per sentence, with its start time (h:mm:ss or m:ss):
""" + "\n".join(lines)


# ----------------------------------------------------------------------------------------- the answer
def _explain(text: str) -> ClaudeUnavailable:
    low = text.lower()
    if any(k in low for k in ("authenticate", "oauth", "not logged in", "log in", "login", "401")):
        return ClaudeUnavailable("Claude is signed out on this PC, so the local rules picked the clips this time.",
                                 hint=SIGN_IN_HINT)
    if any(k in low for k in ("usage limit", "rate limit", "limit reached", "429", "overloaded")):
        return ClaudeUnavailable("Your Claude plan's usage limit is reached, so the local rules picked the clips "
                                 "this time.", hint="Analyse the video again after the limit resets.")
    return ClaudeUnavailable("Claude gave an unexpected answer, so the local rules picked the clips this time.",
                             hint=text[:300])


def _number(value) -> float | None:  # noqa: ANN001
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def parse_answer(printed: str) -> list[Pick]:
    """Read what the command-line tool printed. Raises ClaudeUnavailable when there is nothing usable."""
    try:
        envelope = json.loads(printed)
    except ValueError:
        raise _explain(printed.strip()[:300] or "no output") from None
    if not isinstance(envelope, dict):
        raise _explain(str(envelope)[:300])
    text = str(envelope.get("result") or "")
    if envelope.get("is_error"):
        raise _explain(text or str(envelope)[:300])
    match = re.search(r"\{.*\}", text, flags=re.S)
    try:
        data = json.loads(match.group(0)) if match else None
    except ValueError:
        data = None
    raw = data.get("picks") if isinstance(data, dict) else None
    picks: list[Pick] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        start, end = _number(item.get("start")), _number(item.get("end"))
        if start is None or end is None or end <= start or start < 0:
            continue
        category = str(item.get("category") or "highlight").strip().lower().replace(" ", "_")
        score = _number(item.get("score"))
        picks.append(Pick(start=start, end=end, title=str(item.get("title") or "").strip()[:200],
                          hook=str(item.get("hook") or "").strip()[:120],
                          category=category if category in CATEGORY_LABELS else "highlight",
                          score=round(min(100.0, max(0.0, score if score is not None else 60.0)), 1),
                          reason=str(item.get("reason") or "").strip()[:400]))
    if not picks:
        raise ClaudeUnavailable("Claude returned no picks, so the local rules picked the clips this time.",
                                hint=text[:300])
    return picks


# -------------------------------------------------------------------------------------------- the call
def ask_claude(prompt: str, *, model: str = "sonnet", cli_path: str = "",
               should_cancel: Callable[[], bool] | None = None, timeout: float = TIMEOUT_SECONDS) -> list[Pick]:
    cli = find_cli(cli_path)
    if cli is None:
        raise ClaudeUnavailable("The Claude Code command-line tool was not found on this PC, so the local rules "
                                "picked the clips.", hint="Install Claude Code, sign in, and analyse again.")
    model = model if model in MODELS else "sonnet"
    with tempfile.TemporaryDirectory(prefix="clipforge_claude_") as empty:  # no project files, no CLAUDE.md
        proc = subprocess.Popen(command(cli, model), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, cwd=empty, env=child_env(), text=True, encoding="utf-8",
                                errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except OSError:
            pass
        started = time.monotonic()
        while proc.poll() is None:
            if should_cancel is not None and should_cancel():
                proc.kill()
                raise JobCancelled()
            if time.monotonic() - started > timeout:
                proc.kill()
                raise ClaudeUnavailable("Claude took too long to answer, so the local rules picked the clips.")
            time.sleep(0.5)
        out, err = proc.communicate()
    return parse_answer(out if out.strip() else err)
