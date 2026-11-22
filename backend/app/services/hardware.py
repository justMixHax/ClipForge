"""What this PC can do, established by testing rather than assuming.

A GPU being present is not enough: the NVENC encoder is only used after a real test encode succeeds, and
CUDA is only used for speech-to-text after a model actually loads and runs on it. Anything that fails
its test falls back to the CPU, and the reason is reported.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import site
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil

from ..config import get_settings

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
QUALITIES = ("speed", "balanced", "quality")
_X264 = {"speed": ("veryfast", 22), "balanced": ("fast", 20), "quality": ("slow", 18)}
_NVENC = {"speed": ("p3", 26), "balanced": ("p5", 23), "quality": ("p7", 21)}
_GPU_MODELS = ["large-v3-turbo", "large-v3", "medium", "small", "small.en", "base.en", "tiny.en"]
_CPU_MODELS = {"speed": ["base.en", "small.en", "tiny.en", "small", "medium", "large-v3-turbo", "large-v3"],
               "balanced": ["small.en", "small", "base.en", "tiny.en", "medium", "large-v3-turbo", "large-v3"],
               "quality": ["small.en", "small", "medium", "base.en", "tiny.en", "large-v3-turbo", "large-v3"]}


def encoder_args(encoder: str, quality: str = "balanced") -> list[str]:
    """FFmpeg video encoder arguments for a speed/quality priority."""
    quality = quality if quality in QUALITIES else "balanced"
    if encoder == "h264_nvenc":
        preset, cq = _NVENC[quality]
        return ["-c:v", "h264_nvenc", "-preset", preset, "-tune", "hq", "-rc", "vbr", "-cq", str(cq), "-b:v", "0",
                "-maxrate", "12M", "-bufsize", "24M", "-profile:v", "high", "-bf", "2"]
    preset, crf = _X264[quality]
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-profile:v", "high"]


def cpu_info() -> dict[str, Any]:
    name = platform.processor() or "Unknown CPU"
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                name = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    threads = psutil.cpu_count(logical=True) or 1
    return {"name": name, "cores": psutil.cpu_count(logical=False) or threads, "threads": threads}


def memory_info() -> dict[str, float]:
    vm = psutil.virtual_memory()
    return {"total_gb": round(vm.total / 1024**3, 1), "available_gb": round(vm.available / 1024**3, 1),
            "used_percent": float(vm.percent)}


def storage_info() -> dict[str, Any]:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(settings.data_dir)
    return {"path": str(settings.data_dir), "free_gb": round(usage.free / 1024**3, 1),
            "total_gb": round(usage.total / 1024**3, 1)}


def gpu_info() -> list[dict[str, Any]]:
    """NVIDIA GPUs with live memory and load, via nvidia-smi. Empty when there is none."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        proc = subprocess.run(
            [exe, "--query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return []
    gpus = []
    for line in proc.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6:
            continue

        def number(text: str) -> float | None:
            try:
                return float(text)
            except ValueError:
                return None

        gpus.append({"name": parts[0], "memory_total_mb": number(parts[1]), "memory_used_mb": number(parts[2]),
                     "utilization_percent": number(parts[3]), "temperature_c": number(parts[4]),
                     "driver": parts[5]})
    return gpus


def ffmpeg_version() -> str:
    try:
        proc = subprocess.run([get_settings().ffmpeg_path, "-hide_banner", "-version"], capture_output=True,
                              text=True, timeout=15, creationflags=NO_WINDOW)
        return proc.stdout.splitlines()[0].replace("ffmpeg version ", "").split(" Copyright")[0] if proc.stdout else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def test_encoder(name: str) -> tuple[bool, str]:
    """Encode a third of a second of test video with this encoder. True only if it really works."""
    cmd = [get_settings().ffmpeg_path, "-hide_banner", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
           "testsrc2=s=256x256:r=30", "-t", "0.3", "-c:v", name, "-pix_fmt", "yuv420p", "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40,
                              creationflags=NO_WINDOW)
    except FileNotFoundError:
        return False, "FFmpeg was not found"
    except subprocess.TimeoutExpired:
        return False, "the test encode timed out"
    if proc.returncode == 0:
        return True, "test encode succeeded"
    return False, (proc.stderr.strip().splitlines() or ["test encode failed"])[-1][:300]


def cuda_dll_dirs() -> list[Path]:
    """Folders of the optional NVIDIA runtime packages (cuBLAS, cuDNN) installed into this environment."""
    found: list[Path] = []
    for root in {*site.getsitepackages(), site.getusersitepackages()}:
        nvidia = Path(root) / "nvidia"
        if nvidia.is_dir():
            found.extend(p for p in nvidia.glob("*/bin") if p.is_dir())
    return found


def register_cuda_dlls() -> None:
    """Make those runtime libraries loadable in this process. Harmless when they are not installed."""
    for folder in cuda_dll_dirs():
        if str(folder) not in os.environ.get("PATH", ""):
            os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(str(folder))
            except OSError:
                pass


_CUDA_PROBE = """
import sys, numpy as np
from app.services.hardware import register_cuda_dlls
register_cuda_dlls()
from faster_whisper import WhisperModel
model = WhisperModel(sys.argv[1], device="cuda", compute_type="float16")
segments, _ = model.transcribe(np.zeros(16000, dtype=np.float32), language="en", beam_size=1)
list(segments)
print("CUDA_OK")
"""


def test_whisper_cuda(model_dir: Path | None, gpus: list[dict[str, Any]]) -> tuple[bool, str]:
    """Load a speech model on the GPU and run it once, in a separate process so a driver problem cannot
    take the app down."""
    if not gpus:
        return False, "No NVIDIA GPU was found"
    if model_dir is None:
        return False, "No speech model is installed to test with"
    backend_dir = Path(__file__).resolve().parents[2]
    try:
        proc = subprocess.run([sys.executable, "-c", _CUDA_PROBE, str(model_dir)], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=240, cwd=backend_dir,
                              creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        return False, "The GPU test did not finish in time"
    if proc.returncode == 0 and "CUDA_OK" in proc.stdout:
        return True, "A speech model loaded and ran on the GPU"
    reason = (proc.stderr.strip().splitlines() or ["unknown error"])[-1]
    if "cublas" in reason.lower() or "cudnn" in reason.lower() or "not found" in reason.lower():
        reason += " (the NVIDIA runtime libraries are not installed: run scripts\\enable-gpu.ps1)"
    return False, reason[:400]


def detect(force: bool = False) -> dict[str, Any]:
    """Full hardware report. Cached in data/hardware.json; pass force=True to run the tests again."""
    from ..editing.reframe import detector_available
    from ..transcription import models

    settings = get_settings()
    cache = settings.data_dir / "hardware.json"
    if not force and cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    gpus = gpu_info()
    encoders = {}
    for name in ("libx264", "h264_nvenc"):
        ok, detail = test_encoder(name) if (name == "libx264" or gpus) else (False, "No NVIDIA GPU was found")
        encoders[name] = {"ok": ok, "detail": detail}
    installed = models.installed_models()
    probe_model = models.model_dir(installed[0]) if installed else None
    cuda_ok, cuda_detail = test_whisper_cuda(probe_model, gpus)
    report = {
        "detected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cpu": cpu_info(), "memory": memory_info(),
        "gpus": [{k: g[k] for k in ("name", "memory_total_mb", "driver")} for g in gpus],
        "ffmpeg": ffmpeg_version(), "encoders": encoders,
        "whisper_cuda": {"ok": cuda_ok, "detail": cuda_detail},
        "face_detector": detector_available(), "storage": storage_info(),
    }
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def recommend(hw: dict[str, Any], priority: str, installed_models: list[str]) -> dict[str, Any]:
    """Processing settings for this PC. The GPU is only chosen for what it has passed a test for."""
    priority = priority if priority in QUALITIES else "balanced"
    notes: list[str] = []
    has_gpu = bool(hw.get("gpus"))
    nvenc = hw.get("encoders", {}).get("h264_nvenc", {})
    cuda = hw.get("whisper_cuda", {})
    encoder = "h264_nvenc" if nvenc.get("ok") else "libx264"
    device, compute = ("cuda", "float16") if cuda.get("ok") else ("cpu", "int8")
    if has_gpu and not cuda.get("ok"):
        notes.append(f"GPU found but not used for speech-to-text: {cuda.get('detail', 'test failed')}. Using the CPU.")
    if has_gpu and not nvenc.get("ok"):
        notes.append(f"GPU video encoding failed its test ({nvenc.get('detail', '')}). Using x264 on the CPU.")
    if not has_gpu:
        notes.append("No NVIDIA GPU: everything runs on the CPU.")
    order = _GPU_MODELS if device == "cuda" else _CPU_MODELS[priority]
    model = next((m for m in order if m in installed_models), installed_models[0] if installed_models else None)
    if model is None:
        notes.append("No speech model is installed yet. Install one in Settings.")
    return {"priority": priority, "video_encoder": encoder, "whisper_device": device, "whisper_compute": compute,
            "whisper_model": model, "notes": notes}


def live_usage() -> dict[str, Any]:
    """Current load, for the dashboard."""
    gpus = gpu_info()
    return {"cpu_percent": psutil.cpu_percent(interval=None), "memory": memory_info(),
            "gpu": gpus[0] if gpus else None}
