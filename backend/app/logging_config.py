"""Structured logging: one JSON object per line in data/logs, readable text on the console.

Anything that looks like a credential is masked before it is written, so tokens and API keys cannot leak
into log files even if a caller logs a whole request by mistake.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

_VALUE = r"[^\s\"',}&]+"
# (pattern, replacement). Order matters: the Authorization header is handled first so that the scheme word
# ("Bearer", "Basic") is not mistaken for the secret itself, leaving the real token behind it in clear.
_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?)(?:(bearer|basic|token|digest)\s+)?" + _VALUE),
     r"\1\2 ***"),
    # a bearer token mentioned on its own; a real token is not a plain word, so require a digit or symbol
    (re.compile(r"(?i)\b(bearer)\s+(?=[A-Za-z0-9._~+/=\-]*[0-9._~+/=\-])[A-Za-z0-9._~+/=\-]{8,}"), r"\1 ***"),
    (re.compile(r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|client[_-]?secret|password)"
                r"(\"?'?\s*[:=]\s*\"?'?)" + _VALUE), r"\1\2***"),
    # credentials carried in a web address:  ...?key=...&token=...
    (re.compile(r"(?i)([?&](?:key|token|sig|signature|code)=)[^&\s\"']+"), r"\1***"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}"), "sk-***"),              # OpenAI / Anthropic style keys
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"), "AIza***"),            # Google API keys
    (re.compile(r"\bya29\.[0-9A-Za-z._\-]{10,}"), "ya29.***"),        # Google OAuth access tokens
]


def redact(text: str) -> str:
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text.replace("  ***", " ***")  # tidy the gap left when a header had no scheme word


_STANDARD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD and not key.startswith("_"):
                payload[key] = value if isinstance(value, (int, float, bool, type(None))) else redact(str(value))
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        line = f"{ts} {record.levelname:<7} {record.name}: {redact(record.getMessage())}"
        if record.exc_info:
            line += "\n" + redact(self.formatException(record.exc_info))
        return line


def setup_logging(logs_dir: Path, process_name: str, level: str = "INFO") -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level.upper())
    for handler in list(root.handlers):
        root.removeHandler(handler)

    file_handler = RotatingFileHandler(logs_dir / f"{process_name}.log", maxBytes=5_000_000, backupCount=3,
                                       encoding="utf-8")
    file_handler.setFormatter(JsonFormatter())
    root.addHandler(file_handler)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(ConsoleFormatter())
    root.addHandler(console)

    for noisy in ("httpx", "httpcore", "faster_whisper", "uvicorn.access", "multipart", "alembic.runtime.plugins"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # Every start checks the database schema. Keep the lines that say a migration ran; drop the two
    # lines of boilerplate that are printed even when there is nothing to do.
    migration = logging.getLogger("alembic.runtime.migration")
    if not any(isinstance(f, _MigrationBoilerplate) for f in migration.filters):
        migration.addFilter(_MigrationBoilerplate())


class _MigrationBoilerplate(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not record.getMessage().startswith(("Context impl", "Will assume"))
