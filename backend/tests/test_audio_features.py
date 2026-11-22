from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from app.analysis.audio import AudioFeatures, compute_audio_features

SR = 16000


def tone(seconds: float, amplitude: float, freq: float = 220.0) -> np.ndarray:
    """A steady tone with a little noise, standing in for speech at a given level."""
    rng = np.random.default_rng(7)
    t = np.arange(int(seconds * SR)) / SR
    return (amplitude * np.sin(2 * np.pi * freq * t) + amplitude * 0.05 * rng.standard_normal(t.size)).astype(np.float32)


def write_wav(path: Path, samples: np.ndarray) -> Path:
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm.tobytes())
    return path


def test_audio_at_a_constant_level_has_no_loud_events_however_loud_it_is(tmp_path: Path):
    feats = compute_audio_features(write_wav(tmp_path / "a.wav", tone(60, 0.6)))

    assert feats.loud_events == []
    assert float(np.abs(feats.delta_db).max()) < 3.0


def test_a_burst_well_above_the_local_level_is_a_loud_event(tmp_path: Path):
    signal = np.concatenate([tone(30, 0.05), tone(2, 0.5), tone(28, 0.05)])
    feats = compute_audio_features(write_wav(tmp_path / "a.wav", signal))

    assert len(feats.loud_events) == 1
    event = feats.loud_events[0]
    assert event["start"] == pytest.approx(30.0, abs=0.4)
    assert event["end"] == pytest.approx(32.0, abs=0.4)
    assert event["peak_db"] > 12


def test_loudness_is_judged_against_the_local_baseline_not_an_absolute_level(tmp_path: Path):
    signal = np.concatenate([tone(90, 0.02), tone(90, 0.3)])
    feats = compute_audio_features(write_wav(tmp_path / "a.wav", signal))

    quiet_half = feats.stats(40, 50)
    loud_half = feats.stats(130, 140)
    assert abs(quiet_half["mean_delta"]) < 3
    assert abs(loud_half["mean_delta"]) < 3


def test_silences_are_reported_with_their_times(tmp_path: Path):
    signal = np.concatenate([tone(20, 0.1), tone(3, 0.0004), tone(17, 0.1)])
    feats = compute_audio_features(write_wav(tmp_path / "a.wav", signal))

    assert len(feats.silences) == 1
    start, end = feats.silences[0]
    assert start == pytest.approx(20.0, abs=0.3) and end == pytest.approx(23.0, abs=0.3)
    assert feats.stats(19, 24)["silence_ratio"] == pytest.approx(0.6, abs=0.1)


def test_steady_audio_without_gaps_reports_no_silence(tmp_path: Path):
    feats = compute_audio_features(write_wav(tmp_path / "a.wav", tone(30, 0.1)))

    assert feats.silences == []
    assert feats.stats(0, 30)["silence_ratio"] == 0.0


def test_window_stats_locate_the_peak(tmp_path: Path):
    signal = np.concatenate([tone(30, 0.05), tone(2, 0.5), tone(28, 0.05)])
    feats = compute_audio_features(write_wav(tmp_path / "a.wav", signal))

    stats = feats.stats(20, 40)
    assert stats["peak_delta"] > 12
    assert 29.5 <= stats["peak_time"] <= 32.5
    assert stats["dyn_range"] > 10


def test_features_survive_a_round_trip_to_disk(tmp_path: Path):
    signal = np.concatenate([tone(10, 0.05), tone(1, 0.5), tone(10, 0.05)])
    feats = compute_audio_features(write_wav(tmp_path / "a.wav", signal))
    feats.save(tmp_path / "audio.npz")
    loaded = AudioFeatures.load(tmp_path / "audio.npz")

    assert np.allclose(loaded.rms_db, feats.rms_db)
    assert np.allclose(loaded.delta_db, feats.delta_db)
    assert loaded.loud_events == feats.loud_events
    assert loaded.silences == feats.silences
    assert loaded.duration == pytest.approx(feats.duration)


def test_very_short_audio_does_not_crash(tmp_path: Path):
    feats = compute_audio_features(write_wav(tmp_path / "a.wav", tone(0.05, 0.1)))

    assert feats.loud_events == [] and feats.silences == []
    assert feats.stats(0, 1)["peak_delta"] == 0.0
