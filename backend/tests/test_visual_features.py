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
