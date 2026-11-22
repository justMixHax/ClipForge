"""Quality checks run on every finished export, on the real file.

Each check returns pass, warn or fail with a plain explanation, so a broken or dull export is caught
before it is posted: unplayable file, wrong size, wrong length, missing or silent audio, black frames,
long silences, caption overflow, and cuts that land inside a word.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..errors import ClipForgeError
from ..services import ffmpeg

DURATION_TOLERANCE = 0.35


def _check(key: str, label: str, status: str, detail: str) -> dict[str, str]:
    return {"key": key, "label": label, "status": status, "detail": detail}


def summarize(checks: list[dict[str, str]]) -> str:
    statuses = {c["status"] for c in checks}
    return "fail" if "fail" in statuses else "warn" if "warn" in statuses else "pass"


def run_checks(path: Path, *, expected_w: int, expected_h: int, expected_duration: float, source_has_audio: bool = True,
               caption_report: dict[str, Any] | None = None,
               cuts_into_word: tuple[str, str] | None = None) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []

    # 1. does it open at all
    try:
        info = ffmpeg.probe(path)
    except ClipForgeError as exc:
        checks.append(_check("playable", "Plays back", "fail", f"The file cannot be opened: {exc.message}"))
        return checks

    # 2. one full decode with the detectors attached
    log, decode_error = "", ""
    try:
        log = ffmpeg.run(["-xerror", "-i", str(path), "-vf", "blackdetect=d=0.3:pix_th=0.10",
                          "-af", "silencedetect=n=-45dB:d=2,volumedetect", "-f", "null", "-"], loglevel="info")
    except ffmpeg.FFmpegError as exc:
        decode_error = exc.stderr_tail or str(exc)
        if not info.has_audio:  # the audio detectors cannot attach to a file with no audio: decode video only
            try:
                log = ffmpeg.run(["-xerror", "-i", str(path), "-vf", "blackdetect=d=0.3:pix_th=0.10", "-f", "null", "-"],
                                 loglevel="info")
                decode_error = ""
            except ffmpeg.FFmpegError as exc2:
                decode_error = exc2.stderr_tail or str(exc2)
    if decode_error:
        checks.append(_check("playable", "Plays back", "fail", f"Decoding stopped with an error: {decode_error[:200]}"))
    else:
        checks.append(_check("playable", "Plays back", "pass", f"Decodes cleanly ({info.video_codec}/{info.audio_codec or 'no audio'})."))

    # 3. size
    if (info.width, info.height) == (expected_w, expected_h):
        checks.append(_check("dimensions", "Dimensions", "pass", f"{info.width}x{info.height}"))
    else:
        checks.append(_check("dimensions", "Dimensions", "fail",
                             f"Is {info.width}x{info.height}, expected {expected_w}x{expected_h}."))

    # 4. length
    drift = info.duration - expected_duration
    if abs(drift) <= DURATION_TOLERANCE:
        checks.append(_check("duration", "Length", "pass", f"{info.duration:.2f} s"))
    else:
        checks.append(_check("duration", "Length", "fail",
                             f"Is {info.duration:.2f} s, expected {expected_duration:.2f} s."))

    # 5. audio
    mean = re.search(r"mean_volume:\s*(-?[\d.]+|-inf) dB", log)
    mean_db = None if not mean else (-91.0 if mean.group(1) == "-inf" else float(mean.group(1)))
    if not info.has_audio:
        checks.append(_check("audio", "Audio", "fail", "The file has no audio track."))
    elif not source_has_audio:
        checks.append(_check("audio", "Audio", "warn", "The source has no sound; a silent track was added."))
    elif mean_db is not None and mean_db < -45:
        checks.append(_check("audio", "Audio", "warn", f"Almost silent (average level {mean_db:.0f} dB)."))
    else:
        level = f"average level {mean_db:.0f} dB" if mean_db is not None else "present"
        checks.append(_check("audio", "Audio", "pass", f"Audio {level}."))

    # 6. black frames
    black = sum(float(d) for d in re.findall(r"black_duration:\s*([\d.]+)", log))
    share = black / max(info.duration, 0.1)
    if share > 0.5:
        checks.append(_check("black_frames", "Black frames", "fail", f"{share:.0%} of the clip is black."))
    elif share > 0.1:
        checks.append(_check("black_frames", "Black frames", "warn", f"{black:.1f} s of black frames."))
    else:
        checks.append(_check("black_frames", "Black frames", "pass", "None found." if black == 0 else f"{black:.1f} s."))

    # 7. silence
    silences = [float(d) for d in re.findall(r"silence_duration:\s*([\d.]+)", log)]
    starts = len(re.findall(r"silence_start:", log))
    if starts > len(silences):  # silence ran to the end of the file and was never closed
        silences.append(max(0.0, info.duration - sum(silences)) if not silences else 2.0)
    quiet = min(info.duration, sum(silences))
    if info.has_audio and quiet / max(info.duration, 0.1) > 0.5:
        checks.append(_check("silence", "Silence", "warn", f"{quiet / info.duration:.0%} of the clip is silent."))
    elif silences and max(silences) >= 3.0:
        checks.append(_check("silence", "Silence", "warn", f"Contains a {max(silences):.1f} s stretch of silence."))
    else:
        checks.append(_check("silence", "Silence", "pass", "No long silences."))

    # 8. captions
    report = caption_report or {}
    problems = []
    if report.get("overflow"):
        problems.append("too wide for the frame even after shrinking: " + "; ".join(report["overflow"][:3]))
    if report and report.get("font_found") is False:
        problems.append(f"font '{report.get('font_requested')}' was not found, '{report.get('font')}' was used")
    if problems:
        checks.append(_check("subtitles", "Captions", "warn", "Captions: " + ". ".join(problems) + "."))
    elif report:
        shrunk = f", {report['shrunk']} line(s) shrunk to fit" if report.get("shrunk") else ""
        checks.append(_check("subtitles", "Captions", "pass", f"{report.get('events', 0)} caption states{shrunk}."))
    else:
        checks.append(_check("subtitles", "Captions", "pass", "No captions burned in."))

    # 9. cuts inside a word
    if cuts_into_word:
        edge, word = cuts_into_word
        checks.append(_check("dialogue", "Dialogue", "warn", f"The {edge} of the clip cuts into the word “{word}”."))
    else:
        checks.append(_check("dialogue", "Dialogue", "pass", "Starts and ends between words."))
    return checks
