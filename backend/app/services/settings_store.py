"""User settings (in the database) and encrypted secrets.

Settings are plain values with defaults and validation. Secrets (API keys, tokens) are stored encrypted
and can only be written or checked for presence through this module: there is no function that returns a
secret to the API layer for display.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from ..analysis.presets import LENGTH_RANGES
from ..analysis.chatgpt_review import valid_model
from ..config import get_settings
from ..errors import ClipForgeError
from ..models import Secret, Setting
from ..security.secrets import SecretBox


def _choice(*allowed: str) -> Callable[[Any], bool]:
    return lambda v: isinstance(v, str) and v in allowed


def _text(max_len: int) -> Callable[[Any], bool]:
    return lambda v: isinstance(v, str) and len(v) <= max_len


def _number(low: float, high: float) -> Callable[[Any], bool]:
    return lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and low <= v <= high


def _flag(v: Any) -> bool:
    return isinstance(v, bool)


def _keywords(v: Any) -> bool:
    return isinstance(v, list) and len(v) <= 50 and all(isinstance(k, str) and 0 < len(k) <= 40 for k in v)


# key -> (default, validator)
SCHEMA: dict[str, tuple[Any, Callable[[Any], bool]]] = {
    "processing.priority": ("balanced", _choice("speed", "balanced", "quality")),
    "processing.whisper_model": ("auto", _text(40)),
    "processing.whisper_device": ("auto", _choice("auto", "cpu", "cuda")),
    "processing.video_encoder": ("auto", _choice("auto", "libx264", "h264_nvenc")),
    "processing.language": ("auto", _text(8)),
    "clips.content_preset": ("auto", _choice("auto", "gaming", "streamer", "podcast")),
    "clips.game_style": ("", _text(20)),
    "clips.count": (5, _number(1, 30)),
    "clips.length": ("15-60", _choice(*LENGTH_RANGES)),
    "clips.min_score": (55, _number(0, 100)),
    "clips.aspect": ("9:16", _choice("9:16", "1:1", "4:5", "16:9")),
    "clips.layout": ("auto", _choice("auto", "fill", "fit_blur", "facecam_top", "split")),
    "clips.captions": ("opus", _text(20)),
    "clips.show_hook": (True, _flag),
    "clips.auto_render": (True, _flag),
    "captions.censor": (True, _flag),
    "audio.normalize": (True, _flag),
    "niche.keywords": ([], _keywords),
    "export.folder": ("", _text(400)),
    "ai.engine": ("claude", _choice("claude", "local")),
    "ai.claude_model": ("sonnet", _choice("sonnet", "opus", "haiku")),
    "ai.second_opinion": ("chatgpt", _choice("chatgpt", "off")),
    "ai.chatgpt_model": ("gpt-6.2-astra", lambda v: isinstance(v, str) and valid_model(v)),
    "llm.provider": ("none", _choice("none", "ollama", "openai", "anthropic")),
    "llm.model": ("", _text(80)),
    "llm.base_url": ("http://localhost:11434", _text(200)),
    "llm.allow_paid": (False, _flag),
    "notifications.browser": (True, _flag),
}
SECRET_NAMES = ("llm.openai_api_key", "llm.anthropic_api_key", "youtube.api_key")


def get_all(session: Session) -> dict[str, Any]:
    values = {key: default for key, (default, _) in SCHEMA.items()}
    for row in session.query(Setting).all():
        if row.key in SCHEMA:
            values[row.key] = row.value
    return values


def get(session: Session, key: str) -> Any:
    row = session.get(Setting, key)
    return row.value if row is not None else SCHEMA[key][0]


def set_many(session: Session, values: dict[str, Any]) -> dict[str, Any]:
    for key, value in values.items():
        if key not in SCHEMA:
            raise ClipForgeError(f"Unknown setting: {key}")
        if not SCHEMA[key][1](value):
            raise ClipForgeError(f"Invalid value for {key}")
    for key, value in values.items():
        row = session.get(Setting, key)
        if row is None:
            session.add(Setting(key=key, value=value))
        else:
            row.value = value
    session.commit()
    return get_all(session)


# ------------------------------------------------------------------------------------------- secrets
_box: SecretBox | None = None


def _secret_box() -> SecretBox:
    global _box
    key_path = get_settings().data_dir / "secret.key"
    if _box is None or _box._key_path != key_path:
        _box = SecretBox(key_path)
    return _box


def set_secret(session: Session, name: str, value: str) -> None:
    if name not in SECRET_NAMES:
        raise ClipForgeError(f"Unknown credential: {name}")
    value = value.strip()
    row = session.get(Secret, name)
    if not value:
        if row is not None:
            session.delete(row)
    elif row is None:
        session.add(Secret(name=name, ciphertext=_secret_box().encrypt(value)))
    else:
        row.ciphertext = _secret_box().encrypt(value)
    session.commit()


def read_secret(session: Session, name: str) -> str | None:
    """For backend use only (making the outbound API call). Never pass the result to a response."""
    row = session.get(Secret, name)
    return _secret_box().decrypt(row.ciphertext) if row is not None else None


def secrets_present(session: Session) -> dict[str, bool]:
    stored = {row.name for row in session.query(Secret.name).all()}
    return {name: name in stored for name in SECRET_NAMES}
