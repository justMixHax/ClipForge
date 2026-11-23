from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from app.analysis.visual import VisualFeatures, compute_visual_features

pytestmark = pytest.mark.ffmpeg


def lavfi_video(path: Path, parts: list[tuple[str, float]]) -> Path:
    """Join FFmpeg test sources back to back: [(source, seconds), ...]."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for source, seconds in parts:
        joiner = ":" if "=" in source else "="  # "color=c=red" already has options, "smptebars" has none
        cmd += ["-f", "lavfi", "-t", str(seconds), "-i", f"{source}{joiner}s=320x180:r=30"]
    labels = "".join(f"[{i}:v]" for i in range(len(parts)))
    cmd += ["-filter_complex", f"{labels}concat=n={len(parts)}:v=1:a=0[v]", "-map", "[v]",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def test_a_still_picture_has_no_motion_and_no_cuts(tmp_path: Path):
    video = lavfi_video(tmp_path / "v.mp4", [("color=c=0x336699", 6)])
    feats = compute_visual_features(video, 6.0)

    assert feats.cuts == []
    assert float(feats.motion.max()) < 0.01
    assert feats.stats(0, 6)["static_ratio"] == pytest.approx(1.0)


def test_a_hard_change_of_picture_is_detected_as_a_cut(tmp_path: Path):
    video = lavfi_video(tmp_path / "v.mp4", [("color=c=0x336699", 4), ("smptebars", 4)])
    feats = compute_visual_features(video, 8.0)

    assert len(feats.cuts) == 1
    assert feats.cuts[0] == pytest.approx(4.0, abs=0.6)
    assert feats.stats(0, 8)["cut_count"] == 1
    assert feats.stats(0, 3)["cut_count"] == 0


def test_moving_content_shows_more_activity_than_a_still_picture(tmp_path: Path):
    video = lavfi_video(tmp_path / "v.mp4", [("color=c=0x336699", 5), ("testsrc2", 5)])
    feats = compute_visual_features(video, 10.0)

    still, moving = feats.stats(0.5, 4.5), feats.stats(5.5, 9.5)
    assert moving["motion_mean"] > still["motion_mean"] * 5
    assert moving["activity"] > still["activity"]


def test_black_footage_is_reported_as_dark(tmp_path: Path):
    video = lavfi_video(tmp_path / "v.mp4", [("color=c=black", 4), ("color=c=white", 4)])
    feats = compute_visual_features(video, 8.0)

    assert feats.stats(0.5, 3.5)["dark_ratio"] == pytest.approx(1.0)
    assert feats.stats(4.5, 7.5)["dark_ratio"] == pytest.approx(0.0)


def test_progress_reaches_the_end(tmp_path: Path):
    video = lavfi_video(tmp_path / "v.mp4", [("testsrc2", 4)])
    seen: list[float] = []
    compute_visual_features(video, 4.0, on_progress=seen.append)

    assert seen and seen[-1] == pytest.approx(1.0)


def test_features_survive_a_round_trip_to_disk(tmp_path: Path):
    video = lavfi_video(tmp_path / "v.mp4", [("color=c=0x336699", 3), ("smptebars", 3)])
    feats = compute_visual_features(video, 6.0)
    feats.save(tmp_path / "visual.npz")
    loaded = VisualFeatures.load(tmp_path / "visual.npz")

    assert np.allclose(loaded.motion, feats.motion)
    assert loaded.cuts == feats.cuts
    assert loaded.fps == feats.fps


# ------------------------------------------------------------- faster decoding for long streams
def stream_like_video(path: Path) -> Path:
    """Moving picture then a hard cut, encoded with B-frames the way stream recordings are."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-t", "6", "-i", "testsrc2=s=320x180:r=30",
           "-f", "lavfi", "-t", "6", "-i", "smptebars=s=320x180:r=30",
           "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]",
           "-c:v", "libx264", "-preset", "medium", "-bf", "3", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def test_skipping_frames_nothing_depends_on_measures_the_same(tmp_path: Path):
    video = stream_like_video(tmp_path / "v.mp4")

    fast = compute_visual_features(video, 12.0)
    full = compute_visual_features(video, 12.0, fast_decode=False)

    assert fast.cuts == full.cuts and len(fast.cuts) == 1
    assert fast.motion.size == full.motion.size
    assert float(np.corrcoef(fast.motion[1:], full.motion[1:])[0, 1]) > 0.99
    assert float(np.abs(fast.brightness - full.brightness).max()) < 0.02


def test_the_fast_decoding_is_what_runs_and_a_file_that_refuses_it_is_read_normally(tmp_path: Path, monkeypatch):  # noqa: ANN001
    from app.analysis import visual

    video = stream_like_video(tmp_path / "v.mp4")
    real = visual.ffmpeg.iter_frames
    asked: list[bool] = []

    def refusing(path, **kwargs):  # noqa: ANN001, ANN003, ANN202
        asked.append(bool(kwargs.get("fast_decode")))
        if kwargs.get("fast_decode"):
            return iter(())                       # as if the decoder refused the options: no frames
        return real(path, **kwargs)

    monkeypatch.setattr(visual.ffmpeg, "iter_frames", refusing)

    feats = compute_visual_features(video, 12.0)

    assert asked == [True, False]
    assert feats.motion.size == 24 and len(feats.cuts) == 1
