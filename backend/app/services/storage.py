"""Where files live, and the only code that deletes them.

Everything the app writes is under the data directory, and stored paths are relative to it. The one
exception is a "linked" source video, which stays where the user keeps it and is never modified or
deleted by the app.
"""
from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path
from typing import Any

from ..config import get_settings
from ..errors import ClipForgeError, NotFound
from ..models import Clip, Video
from ..security.paths import PathSecurityError, resolve_inside

CHUNK = 4 * 1024 * 1024


def new_key() -> str:
    return uuid.uuid4().hex


def data_path(relative: str) -> Path:
    """A stored relative path as a real path, refusing anything that would leave the data directory."""
    try:
        return resolve_inside(get_settings().data_dir, relative)
    except PathSecurityError as exc:
        raise ClipForgeError(f"Invalid stored path: {exc}") from exc


def rel_path(path: Path) -> str:
    return path.resolve().relative_to(get_settings().data_dir.resolve()).as_posix()


def video_source_path(video: Video) -> Path:
    path = Path(video.file_path) if video.storage_mode == "linked" else data_path(video.file_path)
    if not path.is_file():
        where = "it was moved or deleted" if video.storage_mode == "linked" else "it is missing from the data folder"
        raise NotFound(f"The source file for “{video.title}” was not found ({where}).",
                       hint="Add the video again, or put the file back where it was.")
    return path


def video_work_dir(video: Video) -> Path:
    folder = get_settings().work_dir / video.storage_key
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def clip_dir(clip: Clip) -> Path:
    folder = get_settings().clips_dir / clip.storage_key
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def fingerprint(path: Path) -> str:
    """A quick identity for duplicate detection: size plus the first and last 4 MB (not the whole file,
    which could be tens of gigabytes)."""
    size = path.stat().st_size
    digest = hashlib.sha256(str(size).encode())
    with open(path, "rb") as fh:
        digest.update(fh.read(CHUNK))
        if size > 2 * CHUNK:
            fh.seek(size - CHUNK)
            digest.update(fh.read(CHUNK))
    return digest.hexdigest()


def remove_tree(path: Path) -> int:
    """Delete a folder that must be inside the data directory. Returns the bytes freed."""
    root = get_settings().data_dir.resolve()
    target = path.resolve()
    if target == root or not target.is_relative_to(root):
        raise ClipForgeError("Refusing to delete a folder outside the data directory")
    if not target.exists():
        return 0
    freed = folder_size(target)
    shutil.rmtree(target, ignore_errors=True)
    return freed


def remove_file(path: Path) -> None:
    root = get_settings().data_dir.resolve()
    if path.resolve().is_relative_to(root):
        path.unlink(missing_ok=True)


def folder_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def usage() -> dict[str, Any]:
    """Disk space used by each kind of file the app keeps."""
    s = get_settings()
    areas = {"uploads": s.uploads_dir, "work": s.work_dir, "clips": s.clips_dir, "models": s.models_dir,
             "exports": s.exports_dir, "logs": s.logs_dir}
    sizes = {name: folder_size(folder) for name, folder in areas.items()}
    disk = shutil.disk_usage(s.data_dir)
    return {"areas": sizes, "total": sum(sizes.values()), "disk_free": disk.free, "disk_total": disk.total,
            "data_dir": str(s.data_dir)}
