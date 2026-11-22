"""Adding a video file to the library, however it arrived: uploaded, linked from disk, or imported
from a link. One place, so every route records the same things."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Video
from . import ffmpeg, storage

log = logging.getLogger("clipforge.library")


def register_video(session: Session, path: Path, *, key: str, mode: str, original_name: str, title: str | None,
                   info: ffmpeg.MediaInfo, thumb_folder: Path, **fields: Any) -> tuple[Video, int | None]:
    """Create the database row for a probed video file, with a thumbnail and a duplicate check.
    Returns (the video, the id of an earlier video with the same content or None)."""
    thumb = ""
    try:
        frame = ffmpeg.grab_frame(path, min(info.duration * 0.1, 5.0), thumb_folder / "thumb.jpg", width=480)
        thumb = storage.rel_path(frame)
    except Exception:  # noqa: BLE001  (a missing thumbnail is not worth failing the import)
        log.warning("thumbnail failed", extra={"file": path.name}, exc_info=True)
    mark = storage.fingerprint(path)
    duplicate = session.scalar(select(Video.id).where(Video.fingerprint == mark).order_by(Video.id).limit(1))
    video = Video(
        title=(title or Path(original_name).stem or "Untitled video")[:300], original_filename=original_name[:300],
        storage_key=key, storage_mode=mode, file_path=str(path) if mode == "linked" else storage.rel_path(path),
        size_bytes=info.size_bytes, fingerprint=mark, duration=info.duration, width=info.width, height=info.height,
        fps=info.fps, video_codec=info.video_codec, audio_codec=info.audio_codec, has_audio=info.has_audio,
        thumb_path=thumb, **fields)
    session.add(video)
    session.commit()
    session.refresh(video)
    return video, duplicate
