"""Build a small, fully synthetic sample video for end-to-end testing.

Everything is generated on this PC: speech comes from the voices built into Windows (System.Speech) and
the pictures come from FFmpeg test sources, so there are no licensing questions and nothing is downloaded.

The script mixes dull filler with four deliberately "clip-worthy" moments (a clutch, a funny story, a hot
take and a fail), and writes the ground truth next to the video so tests can check what the engine found.

Usage:  py -3.12 scripts/sample/make_sample.py [output_dir]
Needs:  ffmpeg on PATH, Windows (for the built-in voices). Standard library only.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

DAVID = "Microsoft David Desktop"
ZIRA = "Microsoft Zira Desktop"

# gain in dB applied to each spoken line: filler is quiet, normal talk in the middle, shouting is loud
QUIET, NORMAL, RAISED, SHOUT = -15.0, -10.0, -5.0, 0.0

# (voice, rate -10..10, gain_db, text, pause_after_seconds)
SECTIONS = [
    {
        "name": "intro_filler", "worthy": False, "visual": "color=c=0x20262e:s=1920x1080:r=30",
        "lines": [
            (DAVID, -1, QUIET, "Okay. So, um, welcome back to the stream, everybody.", 0.7),
            (DAVID, -1, QUIET, "Give me a second. I am just going to fix my audio settings here.", 1.4),
            (DAVID, -1, QUIET, "Yeah. Let me check the settings menu. Okay, I think that is fine now.", 1.8),
        ],
    },
    {
        "name": "clutch", "worthy": True, "visual": "mandelbrot=s=960x540:r=30,scale=1920:1080",
        "lines": [
            (DAVID, 1, NORMAL, "Wait. There is one enemy left, and I have exactly one health point.", 0.5),
            (DAVID, 0, NORMAL, "Do not move. Do not even breathe.", 1.3),
            (DAVID, 3, SHOUT, "No way! No way! I got him! That was a headshot through the wall!", 0.3),
            (DAVID, 3, SHOUT, "Let's go! That is the craziest clutch I have ever hit in my life!", 0.5),
            (ZIRA, 1, RAISED, "Chat is going absolutely insane right now.", 1.6),
        ],
    },
    {
        "name": "loot_filler", "worthy": False, "visual": "smptebars=s=1920x1080:r=30",
        "lines": [
            (DAVID, -1, QUIET, "Alright. Um, so anyway, let me just pick up the loot here.", 0.9),
            (DAVID, -1, QUIET, "I need some ammo. And some bandages. Okay.", 3.2),
            (DAVID, -1, QUIET, "Hmm. Let me check the map.", 2.0),
        ],
    },
    {
        "name": "phone_story", "worthy": True, "visual": "testsrc2=s=1920x1080:r=30",
        "lines": [
            (ZIRA, 0, NORMAL, "Can I tell you the dumbest thing that happened to me yesterday?", 0.4),
            (DAVID, 0, NORMAL, "Go ahead.", 0.4),
            (ZIRA, 0, NORMAL, "I spent forty five minutes looking for my phone.", 0.3),
            (ZIRA, 1, NORMAL, "I checked the couch, I checked the car, I even checked the fridge.", 0.6),
            (ZIRA, 0, RAISED, "And the whole time, I was using the flashlight on my phone to look for it.", 0.5),
            (DAVID, 2, RAISED, "Ha ha ha! You were holding it the entire time? That is incredible.", 1.8),
        ],
    },
    {
        "name": "chat_filler", "worthy": False, "visual": "color=c=0x2a2230:s=1920x1080:r=30",
        "lines": [
            (DAVID, -1, QUIET, "So yeah. Um, thanks for the follow, by the way. Thank you.", 1.2),
            (DAVID, -1, QUIET, "Let me read some chat messages. Hello to everyone joining.", 2.2),
        ],
    },
    {
        "name": "hot_take", "worthy": True, "visual": "rgbtestsrc=s=1920x1080:r=30",
        "lines": [
            (DAVID, 0, NORMAL, "Here is my hot take, and I know people will be mad about this.", 0.4),
            (DAVID, 1, NORMAL, "Open world games are getting way too big. Nobody finishes them.", 0.4),
            (DAVID, 1, NORMAL, "I would rather play a perfect ten hour game than a boring hundred hour game.", 0.4),
            (DAVID, 0, RAISED, "Bigger is not better.", 0.6),
            (ZIRA, 1, RAISED, "That is actually a terrible opinion. Exploring a huge map is the whole point.", 0.4),
            (DAVID, 1, RAISED, "No, you are wrong, and I will die on this hill.", 1.8),
        ],
    },
    {
        "name": "fail", "worthy": True, "visual": "mandelbrot=s=960x540:r=30:start_scale=1.5,scale=1920:1080",
        "lines": [
            (DAVID, 0, NORMAL, "Okay, watch this jump. I have done this a thousand times. Easy.", 1.4),
            (DAVID, 3, SHOUT, "No! No, no, no, no! I fell off the map! I lost everything!", 0.3),
            (DAVID, 2, SHOUT, "Three hours of progress, gone!", 0.6),
            (ZIRA, 0, RAISED, "That might be the worst fail I have ever seen.", 1.8),
        ],
    },
    {
        "name": "outro_filler", "worthy": False, "visual": "color=c=0x1c2a26:s=1920x1080:r=30",
        "lines": [
            (DAVID, -1, QUIET, "Alright, um, I think that is it for today.", 0.8),
            (DAVID, -1, QUIET, "Thanks for watching, everyone. See you tomorrow. Bye.", 1.5),
        ],
    },
]


def run(cmd: list[str], cwd: Path | None = None) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd)
    if proc.returncode != 0:
        raise SystemExit(f"command failed: {cmd[0]}\n{proc.stderr[-1500:]}")


def label_font(tmp: Path) -> str:
    """FFmpeg's drawtext cannot look fonts up by name on Windows (no fontconfig file), and a drive
    letter in a filter needs awkward escaping. Copy a system font next to the work files and refer to
    it by bare file name instead, with FFmpeg running in that folder."""
    import os
    import shutil
    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name in ("arialbd.ttf", "arial.ttf", "segoeui.ttf"):
        if (fonts / name).exists():
            shutil.copyfile(fonts / name, tmp / "label.ttf")
            return "fontfile=label.ttf"
    raise SystemExit("no usable system font found for the sample labels")


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / float(wf.getframerate())


def main() -> None:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / "samples"
    out_dir.mkdir(parents=True, exist_ok=True)
    video_path = out_dir / "clipforge_sample.mp4"
    truth_path = out_dir / "clipforge_sample.truth.json"

    with tempfile.TemporaryDirectory(prefix="clipforge_sample_") as tmp_name:
        tmp = Path(tmp_name)

        # 1. speak every line into its own wav
        manifest, flat = [], []
        for si, section in enumerate(SECTIONS):
            for li, (voice, rate, gain, text, pause) in enumerate(section["lines"]):
                wav = tmp / f"s{si:02d}_l{li:02d}.wav"
                manifest.append({"file": str(wav), "voice": voice, "rate": rate, "text": text})
                flat.append({"section": si, "wav": wav, "gain": gain, "text": text, "pause": pause, "voice": voice})
        manifest_path = tmp / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
             str(Path(__file__).with_name("tts_lines.ps1")), "-Manifest", str(manifest_path)])

        # 2. timeline: where every line and section lands
        t = 0.0
        section_times = [[None, None] for _ in SECTIONS]
        for item in flat:
            dur = wav_seconds(item["wav"])
            item["start"], item["end"] = round(t, 3), round(t + dur, 3)
            si = item["section"]
            if section_times[si][0] is None:
                section_times[si][0] = round(t, 3)
            t += dur + item["pause"]
            section_times[si][1] = round(t, 3)
        total = round(t, 3)

        # 3. one audio track: each line at its level, followed by its pause, over a faint noise bed
        audio_cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
        for item in flat:
            audio_cmd += ["-i", str(item["wav"])]
        audio_cmd += ["-f", "lavfi", "-t", f"{total:.3f}", "-i", "anoisesrc=color=pink:amplitude=0.004:r=48000"]
        parts = []
        for k, item in enumerate(flat):
            parts.append(f"[{k}:a]aresample=48000,volume={item['gain']}dB,apad=pad_dur={item['pause']}[a{k}]")
        joined = "".join(f"[a{k}]" for k in range(len(flat)))
        parts.append(f"{joined}concat=n={len(flat)}:v=0:a=1[speech]")
        parts.append(f"[speech][{len(flat)}:a]amix=inputs=2:duration=first:normalize=0,aformat=channel_layouts=stereo[out]")
        audio_path = tmp / "audio.wav"
        audio_cmd += ["-filter_complex", ";".join(parts), "-map", "[out]", str(audio_path)]
        run(audio_cmd)

        # 4. pictures: one test source per section (so every section is a scene cut), labelled, with a
        #    running source timecode burned in so rendered clips can be checked against their timestamps
        font = label_font(tmp)
        video_cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
        vparts = []
        for si, section in enumerate(SECTIONS):
            dur = section_times[si][1] - section_times[si][0]
            video_cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", section["visual"]]
            label = section["name"].replace("_", " ").upper()
            vparts.append(
                f"[{si}:v]format=yuv420p,drawtext={font}:text='{label}':x=60:y=60:fontsize=64:"
                f"fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=16[v{si}]")
        vjoined = "".join(f"[v{si}]" for si in range(len(SECTIONS)))
        vparts.append(
            f"{vjoined}concat=n={len(SECTIONS)}:v=1:a=0,drawtext={font}:text='%{{pts\\:hms}}':x=60:y=940:"
            f"fontsize=64:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=16[vout]")
        video_cmd += ["-i", "audio.wav", "-filter_complex", ";".join(vparts), "-map", "[vout]",
                      "-map", f"{len(SECTIONS)}:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                      "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "160k", "-shortest",
                      "-movflags", "+faststart", str(video_path.resolve())]
        run(video_cmd, cwd=tmp)

    truth = {
        "duration": total,
        "sections": [
            {"name": s["name"], "worthy": s["worthy"], "start": section_times[i][0], "end": section_times[i][1]}
            for i, s in enumerate(SECTIONS)
        ],
        "lines": [
            {"section": SECTIONS[it["section"]]["name"], "voice": it["voice"], "start": it["start"],
             "end": it["end"], "text": it["text"], "gain_db": it["gain"]}
            for it in flat
        ],
    }
    truth_path.write_text(json.dumps(truth, indent=2), encoding="utf-8")
    print(f"wrote {video_path} ({total:.1f}s) and {truth_path.name}")


if __name__ == "__main__":
    main()
