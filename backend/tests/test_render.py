"""Real renders with FFmpeg on tiny generated videos."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from app.editing.captions import CaptionWord, build_ass, resolve_style
from app.editing.reframe import ASPECTS, FaceSummary, plan_layout
from app.editing.render import RenderRequest, render_clip
from app.errors import JobCancelled
from app.services import ffmpeg
from tests.conftest import make_test_video

pytestmark = pytest.mark.ffmpeg


def colour_blocks(path: Path) -> Path:
    """Six seconds: red for 0-2 s, green for 2-4 s, blue for 4-6 s. Makes cut accuracy visible."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for colour in ("red", "green", "blue"):
        cmd += ["-f", "lavfi", "-t", "2", "-i", f"color=c={colour}:s=640x360:r=30"]
    cmd += ["-f", "lavfi", "-t", "6", "-i", "sine=frequency=440:sample_rate=48000",
            "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]", "-map", "[v]", "-map", "3:a",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def frame_at(video: Path, t: float, size: tuple[int, int] = (108, 192)) -> np.ndarray:
    frames = list(ffmpeg.iter_frames(video, fps=30, width=size[0], height=size[1], pix_fmt="bgr24", start=t,
                                     duration=0.05))
    assert frames, f"no frame at {t}s"
    return frames[0].astype(np.int16)


def request(source: Path, tmp_path: Path, segments, *, layout="fill", aspect="9:16", ass=None, name="out.mp4",
            **plan_kw) -> RenderRequest:
    info = ffmpeg.probe(source)
    plan = plan_layout(info.width, info.height, aspect, layout, FaceSummary(), **plan_kw)
    return RenderRequest(source=source, out_path=tmp_path / name, work_dir=tmp_path / f"work_{name}",
                         segments=segments, src_w=info.width, src_h=info.height, has_audio=info.has_audio,
                         plan=plan, ass_text=ass)


def test_render_makes_a_playable_vertical_mp4_of_the_requested_length(tmp_path: Path):
    source = make_test_video(tmp_path / "src.mp4", seconds=6)
    result = render_clip(request(source, tmp_path, [(1.0, 4.0)]))
    info = ffmpeg.probe(result.path)

    assert (info.width, info.height) == (1080, 1920)
    assert info.duration == pytest.approx(3.0, abs=0.12)
    assert (info.video_codec, info.audio_codec) == ("h264", "aac")
    assert info.fps == pytest.approx(30, abs=0.1)
    assert result.size_bytes == result.path.stat().st_size > 10_000


@pytest.mark.parametrize("aspect", list(ASPECTS))
@pytest.mark.parametrize("layout", ["fill", "fit_blur", "facecam_top"])
def test_every_layout_renders_at_every_shape(tmp_path: Path, aspect: str, layout: str):
    source = make_test_video(tmp_path / "src.mp4", seconds=2)
    extra = {"facecam_box": (20, 20, 200, 150)} if layout == "facecam_top" else {}
    result = render_clip(request(source, tmp_path, [(0.2, 1.4)], layout=layout, aspect=aspect, **extra))
    info = ffmpeg.probe(result.path)

    assert (info.width, info.height) == ASPECTS[aspect]


def test_the_clip_starts_and_ends_on_the_requested_source_frames(tmp_path: Path):
    source = colour_blocks(tmp_path / "blocks.mp4")
    result = render_clip(request(source, tmp_path, [(2.0, 4.0)], layout="fill"))

    def dominant(frame: np.ndarray) -> str:
        b, g, r = (float(frame[..., c].mean()) for c in range(3))
        return max((("blue", b), ("green", g), ("red", r)), key=lambda kv: kv[1])[0]

    assert dominant(frame_at(result.path, 0.0)) == "green"       # not the red that precedes 2.0 s
    assert dominant(frame_at(result.path, 1.0)) == "green"
    assert dominant(frame_at(result.path, 1.9)) == "green"       # not the blue that follows 4.0 s
    assert ffmpeg.probe(result.path).duration == pytest.approx(2.0, abs=0.1)


def test_cut_segments_are_joined_into_one_continuous_clip(tmp_path: Path):
    source = colour_blocks(tmp_path / "blocks.mp4")
    result = render_clip(request(source, tmp_path, [(0.5, 1.5), (4.5, 5.5)]))   # red second, then blue second

    assert ffmpeg.probe(result.path).duration == pytest.approx(2.0, abs=0.12)
    first, second = frame_at(result.path, 0.5), frame_at(result.path, 1.5)
    assert first[..., 2].mean() > first[..., 0].mean() + 50      # red (BGR: index 2)
    assert second[..., 0].mean() > second[..., 2].mean() + 50    # blue


def test_captions_are_burned_into_the_picture_where_the_style_puts_them(tmp_path: Path):
    source = make_test_video(tmp_path / "src.mp4", seconds=3, source="color=c=0x202020")
    words = [CaptionWord("HELLO", 0.2, 1.0), CaptionWord("WORLD", 1.0, 1.8)]
    ass, report = build_ass(words, resolve_style({"preset": "opus", "position": 0.5}), 1080, 1920)

    plain = render_clip(request(source, tmp_path, [(0.0, 2.0)], name="plain.mp4"))
    captioned = render_clip(request(source, tmp_path, [(0.0, 2.0)], ass=ass, name="captioned.mp4"))

    difference = np.abs(frame_at(captioned.path, 0.6) - frame_at(plain.path, 0.6)).mean(axis=2)
    rows = difference.mean(axis=1)
    assert rows[86:106].max() > 20          # text around the vertical centre (192 px tall preview)
    assert rows[:40].max() < 3 and rows[150:].max() < 3
    assert report["font_found"]


def test_the_predicted_caption_width_matches_what_is_actually_drawn(tmp_path: Path):
    """Auto-fit and the overflow check rely on measuring text before rendering: verify the measurement."""
    source = make_test_video(tmp_path / "src.mp4", seconds=2, source="color=c=black")
    style = resolve_style({"preset": "minimal", "font": "Arial Black", "size": 90, "outline": 0, "shadow": 0,
                           "position": 0.5, "uppercase": True, "max_words": 4, "max_chars": 40})
    ass, report = build_ass([CaptionWord("MEASURE", 0.0, 0.9), CaptionWord("THIS", 0.9, 1.9)], style, 1080, 1920)
    result = render_clip(request(source, tmp_path, [(0.0, 1.9)], ass=ass))

    frame = next(iter(ffmpeg.iter_frames(result.path, fps=30, width=1080, height=1920, pix_fmt="gray", start=0.5,
                                         duration=0.05)))
    columns = np.flatnonzero((frame > 128).any(axis=0))
    drawn = columns[-1] - columns[0] + 1

    assert drawn == pytest.approx(report["max_width"], rel=0.08)


def test_a_source_without_sound_still_gets_an_audio_track(tmp_path: Path):
    source = make_test_video(tmp_path / "silent.mp4", seconds=3, with_audio=False)
    result = render_clip(request(source, tmp_path, [(0.5, 2.5)]))
    info = ffmpeg.probe(result.path)

    assert info.has_audio is True
    assert info.duration == pytest.approx(2.0, abs=0.12)


def test_progress_is_reported_up_to_the_end(tmp_path: Path):
    source = make_test_video(tmp_path / "src.mp4", seconds=4)
    seen: list[float] = []
    render_clip(request(source, tmp_path, [(0.0, 3.5)]), on_progress=seen.append)

    assert seen == sorted(seen) and seen[-1] == pytest.approx(1.0, abs=0.02)


def test_cancelling_a_render_leaves_no_file_behind(tmp_path: Path):
    source = make_test_video(tmp_path / "src.mp4", seconds=30, size="1920x1080")
    req = request(source, tmp_path, [(0.0, 29.0)], layout="fit_blur")
    progressed: list[float] = []

    with pytest.raises(JobCancelled):
        render_clip(req, on_progress=progressed.append, should_cancel=lambda: len(progressed) >= 1)

    assert not req.out_path.exists()


def test_a_failed_render_raises_and_leaves_no_file_behind(tmp_path: Path):
    source = make_test_video(tmp_path / "src.mp4", seconds=2)
    req = request(source, tmp_path, [(0.0, 1.0)])
    req.source = tmp_path / "does_not_exist.mp4"

    with pytest.raises(ffmpeg.FFmpegError):
        render_clip(req)
    assert not req.out_path.exists()


def test_segments_must_be_valid(tmp_path: Path):
    source = make_test_video(tmp_path / "src.mp4", seconds=2)
    with pytest.raises(ValueError):
        render_clip(request(source, tmp_path, []))
    with pytest.raises(ValueError):
        render_clip(request(source, tmp_path, [(1.5, 1.0)]))


def _bundled_fonts() -> list[str]:
    from tests.conftest import ROOT
    folder = ROOT / "data" / "fonts"
    return sorted(p.name for p in folder.glob("*.ttf")) if folder.is_dir() else []


@pytest.mark.skipif(not _bundled_fonts(), reason="no fonts in data/fonts (run scripts/get-fonts.ps1)")
def test_downloaded_fonts_are_really_used_in_the_render(tmp_path: Path, data_dir: Path):
    """If the renderer silently fell back to another font, the drawn width would not match the width
    measured from the chosen font file. Checked for every font family in data/fonts."""
    import shutil

    from app.editing import fonts
    from tests.conftest import ROOT

    for name in _bundled_fonts():
        shutil.copyfile(ROOT / "data" / "fonts" / name, data_dir / "fonts" / name)
    fonts.refresh()
    families = [f.name for f in fonts.available_fonts().values() if f.source == "bundled"]
    assert families
    source = make_test_video(tmp_path / "src.mp4", seconds=2, source="color=c=black")
    for family in families:
        style = resolve_style({"preset": "minimal", "font": family, "size": 90, "outline": 0, "shadow": 0, "position": 0.5,
                               "uppercase": True, "max_words": 4, "max_chars": 40})
        ass, report = build_ass([CaptionWord("MEASURE", 0.0, 0.9), CaptionWord("THIS", 0.9, 1.9)], style, 1080, 1920)
        assert report["font"] == family and report["font_found"]
        result = render_clip(request(source, tmp_path, [(0.0, 1.9)], ass=ass, name=f"{family.replace(' ', '_')}.mp4"))
        frame = next(iter(ffmpeg.iter_frames(result.path, fps=30, width=1080, height=1920, pix_fmt="gray", start=0.5,
                                             duration=0.05)))
        columns = np.flatnonzero((frame > 128).any(axis=0))
        drawn = columns[-1] - columns[0] + 1
        assert drawn == pytest.approx(report["max_width"], rel=0.08), f"{family}: drawn {drawn}px, measured {report['max_width']}px"
    fonts.refresh()


# ------------------------------------------------------------------------------------------- sound
def source_with_audio(path: Path, audio: str, seconds: float = 6.0) -> Path:
    """A tiny video whose soundtrack is the given FFmpeg audio source."""
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-t", str(seconds), "-i", "testsrc2=s=320x180:r=30",
                    "-f", "lavfi", "-t", str(seconds), "-i", audio,
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                    "-shortest", str(path)], check=True, capture_output=True)
    return path


def loudness_db(video: Path, tmp_path: Path) -> float:
    """Average level of a file's soundtrack in dB relative to full scale."""
    import wave

    wav = ffmpeg.extract_audio(video, tmp_path / f"{video.stem}_level.wav")
    with wave.open(str(wav)) as fh:
        samples = np.frombuffer(fh.readframes(fh.getnframes()), dtype=np.int16).astype(np.float64) / 32768.0
    return float(20 * np.log10(max(np.sqrt(np.mean(samples ** 2)), 1e-9)))


def test_levelling_the_volume_brings_a_quiet_recording_up_and_a_loud_one_down(tmp_path: Path):
    quiet = source_with_audio(tmp_path / "quiet.mp4", "sine=frequency=330:sample_rate=48000,volume=0.02")
    loud = source_with_audio(tmp_path / "loud.mp4", "sine=frequency=330:sample_rate=48000,volume=6.0")
    assert loudness_db(loud, tmp_path) - loudness_db(quiet, tmp_path) > 30          # very different going in

    levels = []
    for source in (quiet, loud):
        req = request(source, tmp_path, [(0.5, 5.5)], name=f"{source.stem}_out.mp4")
        levels.append(loudness_db(render_clip(req).path, tmp_path))

    assert abs(levels[0] - levels[1]) < 3                                           # about the same coming out
    assert all(-22 < level < -10 for level in levels), levels                       # around the -14 LUFS target


def test_the_volume_is_left_alone_when_levelling_is_switched_off(tmp_path: Path):
    quiet = source_with_audio(tmp_path / "quiet.mp4", "sine=frequency=330:sample_rate=48000,volume=0.02")
    req = request(quiet, tmp_path, [(0.5, 5.5)])
    req.normalize_audio = False

    assert loudness_db(render_clip(req).path, tmp_path) == pytest.approx(loudness_db(quiet, tmp_path), abs=1.5)


def test_noise_reduction_lowers_a_steady_background_hiss(tmp_path: Path):
    hiss = source_with_audio(tmp_path / "hiss.mp4", "anoisesrc=color=white:amplitude=0.03:sample_rate=48000")
    levels = {}
    for denoise in (False, True):
        req = request(hiss, tmp_path, [(0.5, 5.5)], name=f"denoise_{denoise}.mp4")
        req.normalize_audio, req.denoise = False, denoise                           # compare like with like
        levels[denoise] = loudness_db(render_clip(req).path, tmp_path)

    assert levels[True] < levels[False] - 3, levels                                 # at least 3 dB quieter


@pytest.mark.parametrize("fps", [24, 60])
def test_the_chosen_frame_rate_is_what_comes_out(tmp_path: Path, fps: int):
    source = make_test_video(tmp_path / "src.mp4", seconds=3)
    req = request(source, tmp_path, [(0.5, 2.5)])
    req.fps = fps

    info = ffmpeg.probe(render_clip(req).path)

    assert info.fps == pytest.approx(fps, abs=0.1)
    assert info.duration == pytest.approx(2.0, abs=0.12)
