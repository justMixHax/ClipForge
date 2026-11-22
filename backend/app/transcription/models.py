"""Which speech models exist, which are installed locally, and how to install one.

Models live in data/models/whisper/<name>/ as plain folders (model.bin, config.json, tokenizer.json,
vocabulary.txt). Nothing is downloaded unless the user asks for it from Settings or runs the setup
script; a model already on this PC in a Hugging Face style cache is copied instead of downloaded.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from ..config import get_settings

# name -> details. Sizes are approximate download sizes.
WHISPER_MODELS: dict[str, dict] = {
    "tiny.en": {"label": "Tiny (English)", "size_mb": 75, "multilingual": False, "note": "Fastest, least accurate"},
    "base.en": {"label": "Base (English)", "size_mb": 145, "multilingual": False, "note": "Fast drafts"},
    "small.en": {"label": "Small (English)", "size_mb": 480, "multilingual": False,
                 "note": "Good accuracy on CPU. Recommended without a GPU"},
    "small": {"label": "Small (99 languages)", "size_mb": 480, "multilingual": True,
              "note": "Use for non-English videos on CPU"},
    "medium": {"label": "Medium (99 languages)", "size_mb": 1530, "multilingual": True,
               "note": "Slow on CPU; fine on a GPU"},
    "large-v3-turbo": {"label": "Large v3 Turbo (99 languages)", "size_mb": 1620, "multilingual": True,
                       "note": "Best accuracy for the time it takes. Needs a GPU to be practical"},
    "large-v3": {"label": "Large v3 (99 languages)", "size_mb": 3090, "multilingual": True,
                 "note": "Most accurate, slowest. GPU only"},
}
REQUIRED_FILES = ("model.bin", "config.json", "tokenizer.json")


def models_root() -> Path:
    return get_settings().models_dir / "whisper"


def model_dir(name: str) -> Path:
    if name not in WHISPER_MODELS:
        raise ValueError(f"unknown speech model: {name}")
    return models_root() / name


def is_installed(name: str) -> bool:
    folder = model_dir(name)
    return all((folder / f).exists() and (folder / f).stat().st_size > 0 for f in REQUIRED_FILES)


def installed_models() -> list[str]:
    return [name for name in WHISPER_MODELS if is_installed(name)]


def _cache_candidates(name: str) -> list[Path]:
    """Folders where another tool may already have this model (standard Hugging Face cache layouts)."""
    repo = f"models--Systran--faster-whisper-{name}"
    home = Path.home()
    roots = [home / ".cache" / "huggingface" / "hub", home / ".claude" / "skills" / "gta6-clipper" / "models" / "whisper"]
    found: list[Path] = []
    for root in roots:
        snapshots = root / repo / "snapshots"
        if snapshots.is_dir():
            found.extend(p for p in snapshots.iterdir() if (p / "model.bin").exists())
    return found


def find_in_local_caches(name: str) -> Path | None:
    candidates = _cache_candidates(name)
    return candidates[0] if candidates else None


def install_from_cache(name: str) -> bool:
    """Copy the model from an existing local cache. Returns False if there is none."""
    source = find_in_local_caches(name)
    if source is None:
        return False
    target = model_dir(name)
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        if item.is_file() or item.is_symlink():
            shutil.copyfile(item.resolve(), target / item.name)  # snapshots are symlinks into a blob store
    return is_installed(name)


def download(name: str) -> Path:
    """Download from Hugging Face (Systran/faster-whisper-<name>). Only called on explicit user request."""
    from faster_whisper import download_model

    target = model_dir(name)
    target.mkdir(parents=True, exist_ok=True)
    download_model(name, output_dir=str(target))
    return target


def best_installed(prefer_multilingual: bool = False) -> str | None:
    """The most capable installed model, used when the configured one is missing."""
    order = ["large-v3-turbo", "large-v3", "medium", "small", "small.en", "base.en", "tiny.en"]
    installed = set(installed_models())
    if prefer_multilingual:
        for name in order:
            if name in installed and WHISPER_MODELS[name]["multilingual"]:
                return name
    return next((name for name in order if name in installed), None)
