"""Import a video from a web link, using yt-dlp.

Rules this module keeps, whatever link it is given:
  * Public videos only. No cookies, no sign-in, no reading of browser profiles, no .netrc file.
  * No yt-dlp plugins and no yt-dlp configuration files, so nothing outside ClipForge changes how it runs.
  * Nothing is fetched in order to be executed. The JavaScript helper that YouTube needs comes from the
    installed yt-dlp-ejs package and runs in a sandboxed runtime (Deno, or Node with its permission model).
  * One video per link. Playlists and channels are refused; a live stream is refused until it has ended.
  * The caller decides where the file goes. Names that come from the site never reach the file system.

Whether you are allowed to clip the video is not something code can check. The API makes the user say so
before an import starts (see api/videos.py) and that answer is stored with the video.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ..config import ALLOWED_VIDEO_EXTENSIONS, get_settings
from ..errors import ClipForgeError, DependencyMissing, JobCancelled

os.environ.setdefault("YTDLP_NO_PLUGINS", "1")  # read by yt-dlp when it is first imported, which happens below

log = logging.getLogger("clipforge.downloader")
QUALITIES = (720, 1080, 1440, 2160)
# YouTube sometimes refuses a long download part-way (HTTP 403) although the video is fine: asking again for
# fresh download addresses and continuing the partial file works. These are the pauses before each new try.
RETRY_WAITS = (5.0, 15.0, 30.0)
_PASSING_TROUBLE = ("403", "forbidden", "http error 5", "timed out", "connection reset", "incompleteread",
                    "remote end closed", "connection aborted")
MAX_URL_LENGTH = 2000
UPDATE_HINT = "If this site worked before, the downloader may need an update: run scripts\\update-downloader.ps1."
FILE_HINT = "Download the file yourself with an account that may watch it, then add the file to ClipForge."


class LinkError(ClipForgeError):
    """The link cannot be imported. The message says why in plain words."""


@dataclass
class LinkInfo:
    url: str                    # the video's own page, as the site reports it
    title: str
    creator: str
    creator_url: str
    site: str                   # which site handler recognised it, e.g. "Youtube", "TwitchVod", "Generic"
    video_id: str
    duration: float | None      # seconds
    is_live: bool
    license: str                # as stated by the site, when it states one
    approx_bytes: int | None    # size of what would be downloaded at the chosen quality
    width: int | None
    height: int | None
    retries: int = 0            # times the download was refused part-way and continued
    creator_handle: str = ""    # the creator's @name on the site, when it has one (YouTube does)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# ------------------------------------------------------------------------------------- which links
def _is_private_host(host: str) -> bool:
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa")):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved
            or address.is_multicast or address.is_unspecified)


def validate_source_url(raw: str) -> str:
    """Check a pasted link and return it tidied up. Raises LinkError with the reason when it is not a public
    web link."""
    text = (raw or "").strip()
    if not text:
        raise LinkError("Paste the link of a video.")
    if len(text) > MAX_URL_LENGTH:
        raise LinkError("That link is too long to be a video link.")
    if re.match(r"^[A-Za-z]:[\\/]", text) or text.startswith(("\\\\", "//")) or text.lower().startswith("file:"):
        raise LinkError("That is a file on this PC or your network, not a web link.",
                        hint="Use “add a video that is already on this PC” for it.")
    if any(ch.isspace() or ord(ch) < 32 for ch in text):
        raise LinkError("That does not look like a link. Paste the address of one video, with no spaces.")
    scheme = re.match(r"^([A-Za-z][A-Za-z0-9+.\-]*):(?!\d)", text)
    if scheme and scheme.group(1).lower() not in ("http", "https"):
        raise LinkError("Only web links (http or https) can be imported.")
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
        host, port = (parts.hostname or "").lower(), parts.port
    except ValueError:
        raise LinkError("That does not look like a link. Paste the address of one video.") from None
    if parts.scheme.lower() not in ("http", "https"):
        raise LinkError("Only web links (http or https) can be imported.")
    if parts.username or parts.password:
        raise LinkError("Links that contain a user name or password are not accepted: ClipForge does not sign in "
                        "to websites.")
    if host and _is_private_host(host):
        raise LinkError("Links to this PC or to devices on your home network cannot be imported.",
                        hint="To add a file that is on this PC, use its path instead.")
    if not host or ("." not in host and ":" not in host):
        raise LinkError("That does not look like a link. Paste the address of one video.")
    netloc = f"[{host}]" if ":" in host else host
    if port:
        netloc += f":{port}"
    return urlunsplit((parts.scheme.lower(), netloc, parts.path, parts.query, ""))


# ------------------------------------------------------------------------------- what went wrong
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_REASONS: list[tuple[str, str, str]] = [
    (r"sign in to confirm your age|age[- ]restricted|inappropriate for some users",
     "This video is age-restricted, and ClipForge does not sign in to websites, so it cannot be imported from "
     "a link.", FILE_HINT),
    (r"private video|members[- ]only|join this channel|subscribers?[- ]only|requires payment|purchase|rental",
     "This video is not public (private, members-only or paid), so it cannot be imported from a link.", FILE_HINT),
    (r"confirm you.{0,3}re not a bot|sign in to|log ?in required|login required|requires? (a )?login|authentication",
     "The site asked for a sign-in before it would show this video. ClipForge does not sign in to websites.",
     "Sites sometimes do this to automated downloads. Try again later, or download the file yourself and add it."),
    (r"not available in your country|not made this video available in your|geo.?restrict|blocked in your",
     "This video is not available in your country.", ""),
    (r"will begin in|premieres in|has not started|is upcoming|scheduled",
     "This stream or premiere has not started yet.", "Import it after it has ended."),
    (r"unsupported url|no video formats? found|no media found",
     "This link is not a video page that can be imported.",
     "Paste the link of one video (YouTube, a Twitch or Kick recording, and most video sites work), or add the "
     "file itself."),
    (r"getaddrinfo|name or service not known|name resolution|timed out|timeout|connection (refused|reset|aborted)|"
     r"actively refused|network is unreachable|no route to host|ssl|certificate|remote end closed",
     "The site could not be reached.", "Check your internet connection and try again."),
    (r"does not exist|has been removed|been deleted|no longer available|video unavailable|not found|\b404\b|\b410\b",
     "This video could not be found. It may have been removed, or the link may be wrong.", ""),
]


def explain_failure(raw: str) -> LinkError:
    """Turn the downloader's own error text into a message for a person. Advice to hand over browser
    cookies is never passed on."""
    text = _ANSI.sub("", str(raw or "")).strip()
    text = re.sub(r"^(ERROR|WARNING):\s*", "", text)
    lowered = text.lower()
    for pattern, message, hint in _REASONS:
        if re.search(pattern, lowered):
            return LinkError(message, hint=hint)
    detail = re.sub(r"^\[[^\]]+\]\s*[^:\s]*:\s*", "", text.splitlines()[0] if text else "")   # drop "[site] id: "
    detail = re.split(r"(?i)\s*(use --cookies|see\s+https?://\S*cookies|\(caused by)", detail)[0].strip()
    said = f"The site said: {detail[:300]}. " if detail and "cookie" not in detail.lower() else ""
    runtime = "" if js_runtime() else (" YouTube links also need Deno or Node.js 22 or newer on this PC "
                                       "(winget install DenoLand.Deno).")
    return LinkError("This video could not be imported.", hint=f"{said}{UPDATE_HINT}{runtime}")


# ---------------------------------------------------------------------------------- the downloader
def _load():  # noqa: ANN202
    try:
        import yt_dlp
    except ImportError as exc:
        raise DependencyMissing("Importing from a link needs the yt-dlp package, which is not installed.",
                                hint="Run scripts\\setup.ps1.") from exc
    return yt_dlp


def available() -> bool:
    try:
        _load()
    except DependencyMissing:
        return False
    return True


def version() -> str:
    return _load().version.__version__ if available() else ""


# YouTube hands out its video addresses through a small JavaScript puzzle. yt-dlp solves it by running the
# solver from the installed yt-dlp-ejs package in a sandboxed JavaScript runtime that must already be on
# this PC. Other sites do not need one.
_RUNTIME_MINIMUM = {"deno": (2, 3, 0), "node": (22, 0, 0)}


def usable_runtime(name: str, printed: str) -> str:
    """"deno 2.9.8" when `printed` (what `<name> --version` printed) shows a new enough version, else ""."""
    match = re.search(r"(?:deno\s+|^v)(\d+)\.(\d+)\.(\d+)", (printed or "").strip().splitlines()[0] if printed else "")
    if not match:
        return ""
    found = tuple(int(part) for part in match.groups())
    return f"{name} {'.'.join(map(str, found))}" if found >= _RUNTIME_MINIMUM[name] else ""


@lru_cache(maxsize=1)
def js_runtime() -> str:
    """The JavaScript runtime YouTube links will use on this PC ("deno 2.9.8"), or "" when there is none."""
    for name in ("deno", "node"):
        program = shutil.which(name)
        if not program:
            continue
        try:
            done = subprocess.run([program, "--version"], capture_output=True, text=True, timeout=10,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            continue
        if found := usable_runtime(name, done.stdout):
            return found
    return ""


class _Quiet:
    """yt-dlp writes its messages here instead of the console. Warnings go to the log file."""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def debug(self, message: str) -> None:
        pass

    def info(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        self.warnings.append(_ANSI.sub("", str(message)))
        log.info("downloader warning: %s", self.warnings[-1][:300])

    def error(self, message: str) -> None:
        log.info("downloader error: %s", _ANSI.sub("", str(message))[:500])


def _options(max_height: int, folder: Path | None = None) -> dict[str, Any]:
    settings = get_settings()
    options: dict[str, Any] = {
        "quiet": True, "no_warnings": True, "noprogress": True, "color": "no_color", "logger": _Quiet(),
        # one video: a link with a playlist attached gives its video; a bare playlist is refused later
        "noplaylist": True, "playlist_items": "1-2", "extract_flat": "in_playlist",
        # the largest picture up to the chosen height, preferring the codecs every browser and editor plays
        "format": "bv*+ba/b", "format_sort": [f"res:{max_height}", "vcodec:h264", "acodec:aac"],
        "merge_output_format": "mp4",
        "socket_timeout": 30, "retries": 3, "fragment_retries": 5, "extractor_retries": 2,
        "concurrent_fragment_downloads": 4,
        "cachedir": str(settings.work_dir / "downloader-cache"),
        "js_runtimes": {"deno": {}, "node": {}},
        # never: cookies, sign-in, browser profiles, netrc, local files, fetched code, side files
        "cookiefile": None, "cookiesfrombrowser": None, "usenetrc": False, "enable_file_urls": False,
        "remote_components": [], "writethumbnail": False, "writesubtitles": False, "writeinfojson": False,
        "live_from_start": False, "ignoreerrors": False,
    }
    if os.path.isabs(settings.ffmpeg_path):
        options["ffmpeg_location"] = settings.ffmpeg_path
    if folder is not None:
        options.update({"paths": {"home": str(folder), "temp": str(folder)}, "outtmpl": {"default": "source.%(ext)s"},
                        "overwrites": True, "continuedl": True, "windowsfilenames": True})
    return options


def _extract(ydl: Any, url: str, download: bool) -> dict[str, Any]:
    yt_dlp = _load()
    try:
        info = ydl.extract_info(url, download=download)
    except yt_dlp.utils.DownloadCancelled:
        raise
    except yt_dlp.utils.YoutubeDLError as exc:
        raise explain_failure(str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise explain_failure(str(exc)) from exc
    if not info:
        raise LinkError("Nothing that can be imported was found at this link.")
    return info


def _one_video(ydl: Any, url: str) -> tuple[dict[str, Any], str]:
    """(details of the single video behind a link, the link to download it from)."""
    info = _extract(ydl, url, download=False)
    if info.get("_type") == "playlist":
        entries = [entry for entry in (info.get("entries") or []) if entry]
        inner = (entries[0].get("url") or entries[0].get("webpage_url")) if len(entries) == 1 else None
        if not inner:
            raise LinkError("This link is a playlist or a channel, not one video.",
                            hint="Open the video you want and paste its own link.")
        url = inner
        info = _extract(ydl, url, download=False)
        if info.get("_type") == "playlist":
            raise LinkError("This link is a playlist or a channel, not one video.",
                            hint="Open the video you want and paste its own link.")
    return info, url


def _parts(info: dict[str, Any]) -> list[dict[str, Any]]:
    return info.get("requested_formats") or [info]


def _describe(info: dict[str, Any], url: str) -> LinkInfo:
    sizes = [part.get("filesize") or part.get("filesize_approx") for part in _parts(info)]
    approx = int(sum(sizes)) if sizes and all(sizes) else int(info.get("filesize") or info.get("filesize_approx") or 0)
    duration = info.get("duration")
    return LinkInfo(
        url=str(info.get("webpage_url") or url), title=str(info.get("title") or "").strip()[:300] or "Untitled video",
        creator=str(info.get("channel") or info.get("uploader") or info.get("creator") or "").strip()[:200],
        creator_url=str(info.get("channel_url") or info.get("uploader_url") or ""),
        creator_handle=_handle(info.get("uploader_id")),
        site=str(info.get("extractor_key") or ""), video_id=str(info.get("id") or ""),
        duration=float(duration) if duration else None,
        is_live=bool(info.get("is_live")) or info.get("live_status") in ("is_live", "is_upcoming"),
        license=str(info.get("license") or ""), approx_bytes=approx or None,
        width=info.get("width"), height=info.get("height"))


def _handle(uploader_id) -> str:  # noqa: ANN001
    """The uploader id when it is a handle ("@blender"); nothing for a channel id ("UCabc...")."""
    text = str(uploader_id or "").strip()
    return text[:200] if text.startswith("@") and len(text) > 1 and " " not in text else ""


def _refuse_if_unsuitable(found: LinkInfo, max_bytes: int | None) -> None:
    if found.is_live:
        raise LinkError("This stream is live right now.",
                        hint="Import it after it has ended, when the whole recording is available.")
    if max_bytes and found.approx_bytes and found.approx_bytes > max_bytes:
        raise LinkError(f"This video is about {found.approx_bytes / 1024**3:.1f} GB at that quality, over the "
                        f"{max_bytes / 1024**3:g} GB limit for imports.",
                        hint="Choose a lower quality, or download it yourself and link the file from disk.")


def inspect_link(url: str, *, max_height: int = 1080, max_bytes: int | None = None) -> LinkInfo:
    """What is behind a link: title, creator, length, and how big the download would be. Saves nothing."""
    yt_dlp = _load()
    with yt_dlp.YoutubeDL(_options(max_height)) as ydl:
        info, resolved = _one_video(ydl, url)
    found = _describe(info, resolved)
    _refuse_if_unsuitable(found, max_bytes)
    return found


def _passing(text: str) -> bool:
    """A refusal or network hiccup that asking again can fix (not a private or removed video)."""
    low = (text or "").lower()
    return any(sign in low for sign in _PASSING_TROUBLE)


def _pause(seconds: float, should_cancel: Callable[[], bool] | None) -> None:
    """Wait before trying again, stopping at once if the job is cancelled."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if should_cancel is not None and should_cancel():
            raise _load().utils.DownloadCancelled("cancelled")
        time.sleep(min(0.25, max(0.0, end - time.monotonic())))


def _empty(folder: Path) -> None:
    shutil.rmtree(folder, ignore_errors=True)


def download_link(url: str, folder: Path, *, max_height: int = 1080, max_bytes: int | None = None,
                  on_progress: Callable[[float], None] | None = None,
                  should_cancel: Callable[[], bool] | None = None,
                  keep_partial: Callable[[], bool] | None = None,
                  on_found: Callable[[LinkInfo], None] | None = None) -> tuple[Path, LinkInfo]:
    """Download the one video behind a link into `folder` as source.<ext>. Returns (file, details).

    on_found is called with the video's details after the link has been checked and before anything is
    downloaded; raising from it stops the import.

    Raises LinkError with a readable reason, or JobCancelled. On failure or cancellation the folder is
    removed, unless keep_partial() says the partial download should stay so a later run can continue it."""
    yt_dlp = _load()
    folder.mkdir(parents=True, exist_ok=True)
    files: dict[str, tuple[float, float]] = {}
    state = {"best": 0.0, "too_big": False}

    retries = 0
    try:
        ydl = yt_dlp.YoutubeDL(_options(max_height, folder))
        try:
            info, resolved = _one_video(ydl, url)
            found = _describe(info, resolved)
            _refuse_if_unsuitable(found, max_bytes)
            if on_found is not None:
                on_found(found)
            expected, pieces = float(found.approx_bytes or 0), len(_parts(info))

            def hook(update: dict[str, Any]) -> None:
                if should_cancel is not None and should_cancel():
                    raise yt_dlp.utils.DownloadCancelled("cancelled")
                if update.get("status") not in ("downloading", "finished"):
                    return
                done = float(update.get("downloaded_bytes") or 0)
                total = float(update.get("total_bytes") or update.get("total_bytes_estimate") or 0)
                if update["status"] == "finished":
                    done = total = max(done, total)
                files[str(update.get("filename") or update.get("tmpfilename") or "?")] = (done, total)
                got = sum(d for d, _ in files.values())
                if max_bytes and got > max_bytes:
                    state["too_big"] = True
                    raise yt_dlp.utils.DownloadCancelled("over the size limit")
                known = sum(t for _, t in files.values())
                if not expected:  # sizes unknown up front: assume the pieces still to come are like the ones seen
                    known *= max(1.0, pieces / max(1, len(files)))
                state["best"] = max(state["best"], min(0.99, got / max(expected, known, got, 1.0)))
                if on_progress is not None:
                    on_progress(state["best"])

            while True:
                try:
                    if retries:   # fresh addresses; the partial file is continued
                        ydl.close()
                        ydl = yt_dlp.YoutubeDL(_options(max_height, folder))
                        info, resolved = _one_video(ydl, url)
                    ydl.add_progress_hook(hook)
                    if should_cancel is not None and should_cancel():
                        raise yt_dlp.utils.DownloadCancelled("cancelled")
                    result = _extract(ydl, resolved, download=True)
                    break
                except LinkError as exc:
                    said = str(exc.__cause__ or "") or exc.hint
                    if not _passing(said):
                        raise
                    if retries >= len(RETRY_WAITS):
                        raise LinkError(f"The site refused the download {retries + 1} times in a row, so it could not "
                                        "be imported.", hint=f"It said: {said.strip()[:200]}. This happens with "
                                        "YouTube now and then: try again in a few minutes, or choose a lower "
                                        "quality.") from exc
                    log.info("download refused part-way, trying again with fresh addresses: %s", said.strip()[:200])
                    _pause(RETRY_WAITS[retries], should_cancel)
                    retries += 1
        finally:
            ydl.close()
    except yt_dlp.utils.DownloadCancelled:
        if state["too_big"]:
            _empty(folder)
            raise LinkError(f"This video is larger than the {max_bytes / 1024**3:g} GB limit for imports.",
                            hint="Choose a lower quality, or download it yourself and link the file from disk.") from None
        if keep_partial is None or not keep_partial():
            _empty(folder)
        raise JobCancelled() from None
    except BaseException:
        _empty(folder)
        raise

    saved = [Path(item["filepath"]) for item in (result.get("requested_downloads") or []) if item.get("filepath")]
    path = next((p for p in saved if p.is_file()), None) or next(
        (p for p in sorted(folder.glob("source.*")) if p.suffix.lower() in ALLOWED_VIDEO_EXTENSIONS), None)
    if path is None or path.suffix.lower() not in ALLOWED_VIDEO_EXTENSIONS:
        _empty(folder)
        raise LinkError("The site delivered a file ClipForge cannot use"
                        + (f" ({path.suffix})." if path is not None else "."), hint=UPDATE_HINT)
    for leftover in folder.iterdir():  # partial pieces and bookkeeping files of the downloader
        if leftover != path and leftover.is_file():
            leftover.unlink(missing_ok=True)
    if on_progress is not None:
        on_progress(1.0)
    details = _describe(result, resolved)
    details.retries = retries
    return path, details
