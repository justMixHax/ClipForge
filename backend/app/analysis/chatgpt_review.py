"""ChatGPT as the second opinion: after the picks are made (by Claude, or by the local rules when Claude is
unavailable), ChatGPT reads the same transcript, says for each pick whether it agrees, and may add up to two
moments that were missed. The user still decides: a clip ChatGPT disagrees with is kept and marked.

ClipForge talks to ChatGPT through the Codex command-line tool installed with the ChatGPT/Codex app, so it
uses the user's own ChatGPT plan and sign-in. No API key is used: OPENAI_API_KEY and similar settings are
removed from the tool's environment, and the user's own Codex settings are ignored, so a provider set up
elsewhere (such as a local relay) can never take over. The tool runs read-only, without saving a session,
from an empty folder, and its answer is checked before anything is used.
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import ClipForgeError, JobCancelled
from ..transcription.sentences import Sentence
from .claude_review import Pick, clock
from .scoring import CATEGORY_LABELS

DEFAULT_MODEL = "gpt-6.2-astra"
TIMEOUT_SECONDS = 900
MAX_ADDITIONS = 2
SEARCH_ROOTS = (Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI" / "Codex" / "bin",)
SIGN_IN_HINT = "Open the ChatGPT app and make sure you are signed in (or run  codex login  in PowerShell)."
# Everything that would let it act rather than just read and answer: shell, files, browser, apps.
DISABLED_FEATURES = ("shell_tool", "unified_exec", "view_image", "browser_use", "browser_use_external",
                     "in_app_browser", "computer_use", "apps", "plugins", "remote_plugin", "tool_suggest",
                     "sleep_tool")


class ChatGPTUnavailable(ClipForgeError):
    """ChatGPT could not give an answer. The message says why in plain words."""


@dataclass
class Review:
    verdicts: dict[int, tuple[str, str]] = field(default_factory=dict)   # pick number -> (agree|disagree, reason)
    additions: list[Pick] = field(default_factory=list)


MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,59}$")


def valid_model(name: str) -> bool:
    """Model names are plain words like gpt-6.2-astra: nothing that a command line could read as more."""
    return bool(MODEL_NAME.match(name or ""))


def _native(path: str) -> bool:
    # Only a real program: a .cmd or .bat (an npm install) runs through the command interpreter.
    return Path(path).suffix.lower() == ".exe" and Path(path).is_file()


def find_cli(configured: str = "") -> str | None:
    """Where the Codex command-line tool is: the configured path, PATH, or the newest copy the app installed."""
    if configured:
        return configured if _native(configured) else None
    found = shutil.which("codex")
    if found and _native(found):
        return found
    copies = [p for root in SEARCH_ROOTS if root.is_dir() for p in root.glob("*/codex.exe") if p.is_file()]
    return str(max(copies, key=lambda p: p.stat().st_mtime)) if copies else None


def child_env() -> dict[str, str]:
    """This PC's environment minus anything that could make the tool use an API key or another provider
    instead of the ChatGPT sign-in, and minus settings inherited from other AI programs."""
    blocked = ("OPENAI_", "AZURE_OPENAI", "CODEX_API", "ANTHROPIC_", "CLAUDE", "CLIPFORGE_")
    return {k: v for k, v in os.environ.items() if not k.upper().startswith(blocked)}


def command(cli: str, model: str, workdir: Path, answer_file: Path) -> list[str]:
    if not valid_model(model):
        raise ChatGPTUnavailable("The ChatGPT model name in Settings is not valid, so there is no second opinion.",
                                 hint="Use a name like gpt-6.2-astra.")
    disabled = [part for feature in DISABLED_FEATURES for part in ("--disable", feature)]
    return [cli, "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--ephemeral", "--ignore-user-config",
            "--ignore-rules", "--color", "never", "-C", str(workdir), *disabled, "-m", model,
            "-c", 'model_provider="openai"', "-c", 'web_search="disabled"', "-c", 'model_reasoning_effort="medium"',
            "-o", str(answer_file), "-"]


def _stop(proc: subprocess.Popen) -> None:
    """End the tool and wait until it has let go of its files (on Windows they cannot be deleted before)."""
    proc.kill()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        pass


def _login_status(cli: str, should_cancel: Callable[[], bool] | None = None) -> str:
    """What `codex login status` says, e.g. "Logged in using ChatGPT". Can be cancelled while it runs."""
    try:
        proc = subprocess.Popen([cli, "login", "status"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, env=child_env(),
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as exc:
        raise ChatGPTUnavailable("The Codex tool could not be started, so there is no second opinion.",
                                 hint=str(exc)[:200]) from exc
    started = time.monotonic()
    while proc.poll() is None:
        if should_cancel is not None and should_cancel():
            _stop(proc)
            raise JobCancelled()
        if time.monotonic() - started > 30:
            _stop(proc)
            raise ChatGPTUnavailable("The Codex tool did not say whether it is signed in, so there is no second "
                                     "opinion.")
        time.sleep(0.2)
    out = proc.stdout.read() if proc.stdout else b""
    return out.decode("utf-8", errors="replace").strip()


def require_chatgpt_sign_in(cli: str, should_cancel: Callable[[], bool] | None = None) -> None:
    """Refuse to send anything unless Codex is signed in with a ChatGPT account (never an API key)."""
    status = _login_status(cli, should_cancel)
    if "using chatgpt" in status.lower():
        return
    if "api key" in status.lower():
        raise ChatGPTUnavailable("Codex is signed in with an API key, not your ChatGPT plan, so ClipForge sends it "
                                 "nothing.", hint="Run  codex logout  then sign in to the ChatGPT app.")
    raise ChatGPTUnavailable("ChatGPT is signed out on this PC, so there is no second opinion this time.",
                             hint=SIGN_IN_HINT)


# ------------------------------------------------------------------------------------------- the prompt
def build_prompt(sentences: Sequence[Sentence], picks: Sequence[Any], options, *, title: str,  # noqa: ANN001
                 creator: str, chosen_by_user: bool = False) -> str:
    """The review request. For a moment the user chose themselves (chosen_by_user), ChatGPT only gives its verdict
    on it, and only the transcript around it is sent."""
    if chosen_by_user and picks:
        first, last = min(p.start for p in picks) - 60, max(p.end for p in picks) + 30
        sentences = [s for s in sentences if s.end >= first and s.start <= last]
    lines = [f"[{clock(s.start)}] {s.text}" for s in sentences]
    numbered = "\n".join(
        f"{n}. {clock(p.start)}-{clock(p.end)} \"{p.title}\" ({p.category}, score {p.score:.0f}): "
        f"{(p.explanation or '').replace('Picked by Claude: ', '')}" for n, p in enumerate(picks, 1)) or "(none)"
    by = f" by {creator}" if creator else ""
    if chosen_by_user:
        return f"""You are the second opinion in ClipForge, a tool that cuts long videos into short vertical clips for
TikTok, YouTube Shorts and Instagram Reels. The moment below was chosen by the user while watching the video.
Review it honestly. Work only from the text given here; do not run commands or open files.

Video: "{title}"{by}.

The moment:
{numbered}

Say "agree" if it would work as a short on its own (funny, surprising, tense, impressive or interesting, with its
setup and payoff inside), or "disagree" if it would not, with one short reason. Do not suggest other moments.

Answer with JSON only:
{{"verdicts": [{{"pick": 1, "verdict": "agree", "reason": "one short sentence"}}], "additions": []}}

Transcript around the moment, one line per sentence, with its start time:
""" + "\n".join(lines)
    return f"""You are the second opinion in ClipForge, a tool that cuts long videos into short vertical clips for
TikTok, YouTube Shorts and Instagram Reels. Another AI already picked the clips below from this video's
transcript. Review them honestly. Work only from the text given here; do not run commands or open files.

Video: "{title}"{by}.

The picks:
{numbered}

For each pick, say "agree" if it would work as a short on its own (funny, surprising, tense, impressive or
interesting, with its setup and payoff inside), or "disagree" if it would not, with one short reason.
Then, under "additions", give at most {MAX_ADDITIONS} clearly better moments that were missed (between
{options.min_len:g} and {options.max_len:g} seconds, not overlapping the picks), or none.

Rules: use only times from the transcript. Never invent words, quotes or events. Category is one of:
{', '.join(CATEGORY_LABELS)}. Scores are honest 0-100 estimates, not promises.

Answer with JSON only:
{{"verdicts": [{{"pick": 1, "verdict": "agree", "reason": "one short sentence"}}],
"additions": [{{"start": 251.0, "end": 301.5, "title": "short title", "hook": "on-screen line, max 12 words",
"category": "funny", "score": 80, "reason": "one short sentence"}}]}}
Times are in seconds from the start of the video.

Transcript, one line per sentence, with its start time:
""" + "\n".join(lines)


# ----------------------------------------------------------------------------------------- the answer
def _explain(text: str) -> ChatGPTUnavailable:
    low = text.lower()
    if any(k in low for k in ("401", "unauthorized", "not logged in", "log in", "login", "authentication",
                              "sign in")):
        return ChatGPTUnavailable("ChatGPT is signed out on this PC, so there is no second opinion this time.",
                                  hint=SIGN_IN_HINT)
    if any(k in low for k in ("usage limit", "rate limit", "limit reached", "429", "quota")):
        return ChatGPTUnavailable("Your ChatGPT plan's limit is reached, so there is no second opinion this time.",
                                  hint="Analyse again after the limit resets.")
    return ChatGPTUnavailable("ChatGPT gave an unexpected answer, so there is no second opinion this time.",
                              hint=text[:300])


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def parse_answer(text: str) -> Review:
    """Read ChatGPT's answer. Raises ChatGPTUnavailable when there is nothing usable."""
    match = re.search(r"\{.*\}", text or "", flags=re.S)
    try:
        data = json.loads(match.group(0)) if match else None
    except ValueError:
        data = None
    if not isinstance(data, dict) or not ({"verdicts", "additions"} & set(data)):
        raise _explain(text or "no answer")
    if not isinstance(data.get("verdicts", []), list) or not isinstance(data.get("additions", []), list):
        raise ChatGPTUnavailable("ChatGPT answered in an unexpected shape, so there is no second opinion this time.",
                                 hint=text[:300])
    review = Review()
    for item in data.get("verdicts") or []:
        if not isinstance(item, dict):
            continue
        number, verdict = item.get("pick"), str(item.get("verdict") or "").strip().lower()
        if isinstance(number, bool) or not isinstance(number, int) or verdict not in ("agree", "disagree"):
            continue
        review.verdicts[number] = (verdict, str(item.get("reason") or "").strip()[:300])
    for item in data.get("additions") or []:
        if not isinstance(item, dict):
            continue
        start, end = _number(item.get("start")), _number(item.get("end"))
        if start is None or end is None or end <= start or start < 0:
            continue
        category = str(item.get("category") or "highlight").strip().lower().replace(" ", "_")
        score = _number(item.get("score"))
        review.additions.append(Pick(
            start=start, end=end, title=str(item.get("title") or "").strip()[:200],
            hook=str(item.get("hook") or "").strip()[:120],
            category=category if category in CATEGORY_LABELS else "highlight",
            score=round(min(100.0, max(0.0, score if score is not None else 60.0)), 1),
            reason=str(item.get("reason") or "").strip()[:400]))
    return review


# -------------------------------------------------------------------------------------------- the call
def run_codex(prompt: str, *, model: str = DEFAULT_MODEL, cli_path: str = "",
              should_cancel: Callable[[], bool] | None = None, timeout: float = TIMEOUT_SECONDS) -> str:
    """Send a prompt to ChatGPT through the Codex tool and return its final answer as text."""
    cli = find_cli(cli_path)
    if cli is None:
        raise ChatGPTUnavailable("The Codex tool from the ChatGPT app was not found on this PC, so there is no "
                                 "second opinion.", hint="Install the ChatGPT (Codex) app and sign in.")
    require_chatgpt_sign_in(cli, should_cancel)
    if should_cancel is not None and should_cancel():
        raise JobCancelled()
    with tempfile.TemporaryDirectory(prefix="clipforge_chatgpt_") as empty:
        workdir = Path(empty)
        answer_file = workdir / "answer.txt"
        log_file = workdir / "codex.log"
        prompt_file = workdir.parent / f"{workdir.name}.prompt.txt"   # outside the folder the tool works in
        prompt_file.write_text(prompt, encoding="utf-8")
        try:
            # the prompt comes from a file, so a tool that stalls can never block ClipForge while it is sent
            with open(log_file, "w", encoding="utf-8", errors="replace") as log, open(prompt_file, "rb") as source:
                try:
                    proc = subprocess.Popen(command(cli, model or DEFAULT_MODEL, workdir, answer_file), stdin=source,
                                            stdout=log, stderr=subprocess.STDOUT, cwd=empty, env=child_env(),
                                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                except OSError as exc:
                    raise ChatGPTUnavailable("The Codex tool could not be started, so there is no second opinion.",
                                             hint=str(exc)[:200]) from exc
                started = time.monotonic()
                while proc.poll() is None:
                    if should_cancel is not None and should_cancel():
                        _stop(proc)
                        raise JobCancelled()
                    if time.monotonic() - started > timeout:
                        _stop(proc)
                        raise ChatGPTUnavailable("ChatGPT took too long to answer, so there is no second opinion.")
                    time.sleep(0.5)
        finally:
            prompt_file.unlink(missing_ok=True)
        answer = answer_file.read_text(encoding="utf-8", errors="replace") if answer_file.exists() else ""
        if proc.returncode != 0 or not answer.strip():
            raise _explain(log_file.read_text(encoding="utf-8", errors="replace")[-2000:] or "no answer")
    return answer


def ping(model: str = DEFAULT_MODEL, timeout: float = 120) -> str:
    """A one-word test of the connection. Returns ChatGPT's reply, or raises ChatGPTUnavailable."""
    return run_codex("Reply with exactly: OK", model=model, timeout=timeout).strip()[:100]


def ask_chatgpt(prompt: str, **kwargs: Any) -> Review:
    return parse_answer(run_codex(prompt, **kwargs))
