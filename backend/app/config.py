"""Runtime configuration.

Values come from environment variables prefixed with CLIPFORGE_ or from a .env file in the project root
(environment variables win). CLIPFORGE_ENV_FILE names a different settings file; set it to an empty value
to read none at all, which is what the tests do so the user's own .env can never change their outcome.
Everything the app writes lives under one data directory, and the database stores paths relative to it,
so the whole project folder can be moved without breaking anything.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent

ALLOWED_VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".webm", ".m4v")
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CLIPFORGE_", env_file=str(PROJECT_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    host: str = "127.0.0.1"
    port: int = 3000
    # Binding to anything other than this PC is refused unless this is switched on deliberately.
    allow_remote: bool = False

    data_dir: Path = PROJECT_ROOT / "data"
    frontend_dist: Path = PROJECT_ROOT / "frontend" / "dist"

    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"

    max_upload_gb: float = Field(default=20.0, gt=0)
    log_level: str = "INFO"
    # The API process starts and supervises the processing worker unless this is off.
    spawn_worker: bool = True
    # Extra browser origins allowed to call the API (the Vite dev server during development).
    dev_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "clipforge.db"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"

    @property
    def clips_dir(self) -> Path:
        return self.data_dir / "clips"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def fonts_dir(self) -> Path:
        return self.data_dir / "fonts"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def max_upload_bytes(self) -> int:
        return int(self.max_upload_gb * 1024**3)

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.uploads_dir, self.work_dir, self.clips_dir, self.models_dir,
                  self.fonts_dir, self.logs_dir, self.exports_dir):
            d.mkdir(parents=True, exist_ok=True)

    def allowed_origins(self) -> set[str]:
        origins = {f"http://{h}:{self.port}" for h in ("127.0.0.1", "localhost")}
        origins.update(o.strip() for o in self.dev_origins.split(",") if o.strip())
        return origins


@lru_cache
def get_settings() -> Settings:
    env_file = os.environ.get("CLIPFORGE_ENV_FILE")
    if env_file is None:
        return Settings()
    return Settings(_env_file=env_file or None)  # type: ignore[call-arg]


def check_bind(settings: Settings) -> None:
    """Stop before listening on anything wider than this PC unless that was switched on deliberately.

    There is no login in ClipForge, so anyone who can reach the address can use everything in it."""
    if settings.host not in LOOPBACK_HOSTS and not settings.allow_remote:
        raise SystemExit(f"Refusing to listen on {settings.host}: ClipForge only serves this PC. "
                         "Set CLIPFORGE_ALLOW_REMOTE=true if you really want it reachable from the network.")


def reset_settings_cache() -> None:
    """Used by tests that point the app at a temporary data directory."""
    get_settings.cache_clear()
