from __future__ import annotations

import pytest

from app.services import hardware


@pytest.mark.ffmpeg
def test_a_working_encoder_passes_its_test_encode():
    ok, detail = hardware.test_encoder("libx264")
    assert ok is True, detail


@pytest.mark.ffmpeg
def test_an_encoder_that_does_not_exist_fails_with_a_reason():
    ok, detail = hardware.test_encoder("definitely_not_an_encoder")
    assert ok is False and detail


def test_basic_facts_about_this_pc_are_reported():
    cpu, memory = hardware.cpu_info(), hardware.memory_info()

    assert cpu["threads"] >= cpu["cores"] >= 1 and cpu["name"]
    assert memory["total_gb"] > 0.5 and 0 < memory["available_gb"] <= memory["total_gb"]
    assert hardware.storage_info()["free_gb"] > 0


FAST_GPU = {"cpu": {"cores": 6, "threads": 12}, "memory": {"total_gb": 16},
            "gpus": [{"name": "NVIDIA GeForce RTX 3060", "memory_total_mb": 12288}],
            "encoders": {"h264_nvenc": {"ok": True}, "libx264": {"ok": True}},
            "whisper_cuda": {"ok": True, "detail": "ok"}}


def test_the_gpu_is_only_recommended_when_it_passed_its_tests():
    tested = hardware.recommend(FAST_GPU, "balanced", installed_models=["small.en", "large-v3-turbo"])
    assert tested["video_encoder"] == "h264_nvenc"
    assert (tested["whisper_device"], tested["whisper_compute"]) == ("cuda", "float16")
    assert tested["whisper_model"] == "large-v3-turbo"

    untested = {**FAST_GPU, "encoders": {"h264_nvenc": {"ok": False}, "libx264": {"ok": True}},
                "whisper_cuda": {"ok": False, "detail": "cublas64_12.dll not found"}}
    fallback = hardware.recommend(untested, "balanced", installed_models=["small.en", "large-v3-turbo"])
    assert fallback["video_encoder"] == "libx264"
    assert (fallback["whisper_device"], fallback["whisper_compute"]) == ("cpu", "int8")
    assert fallback["whisper_model"] == "small.en"
    assert any("GPU" in note for note in fallback["notes"])


def test_recommendations_never_name_a_model_that_is_not_installed():
    rec = hardware.recommend(FAST_GPU, "quality", installed_models=["base.en"])
    assert rec["whisper_model"] == "base.en"
    assert hardware.recommend(FAST_GPU, "speed", installed_models=[])["whisper_model"] is None


def test_speed_and_quality_priorities_change_the_encode_settings():
    assert hardware.encoder_args("libx264", "speed") != hardware.encoder_args("libx264", "quality")
    assert "h264_nvenc" in hardware.encoder_args("h264_nvenc", "balanced")
    assert "libx264" in hardware.encoder_args("libx264", "balanced")
