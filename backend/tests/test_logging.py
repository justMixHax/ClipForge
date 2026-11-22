"""Credentials must never reach a log file, even if a caller logs a whole request or error by mistake."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from app.logging_config import redact, setup_logging


@pytest.mark.parametrize(("line", "secret"), [
    ("Authorization: Bearer abcdefghijklmnop123456", "abcdefghijklmnop123456"),
    ("headers={'authorization': 'Bearer abcdefghijklmnop123456'}", "abcdefghijklmnop123456"),
    ("Authorization: Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA"),
    ("api_key=sk-ant-1234567890abcdefghij", "1234567890abcdefghij"),
    ('{"access_token": "ya29.A0ARrdaM-very-secret", "expires_in": 3599}', "ya29.A0ARrdaM-very-secret"),
    ("refresh_token=1//0gLongRefreshTokenValue&grant_type=refresh_token", "1//0gLongRefreshTokenValue"),
    ("client_secret: GOCSPX-abcdefghijklmnop", "GOCSPX-abcdefghijklmnop"),
    ("password=hunter2hunter2", "hunter2hunter2"),
    ("calling the model with key sk-proj1234567890abcdef", "proj1234567890abcdef"),
    ("GET https://www.googleapis.com/youtube/v3/search?key=AIzaSyA1234567890abcdefghijklmnopqrstuv&q=gta",
     "AIzaSyA1234567890abcdefghijklmnopqrstuv"),
])
def test_anything_shaped_like_a_credential_is_masked(line: str, secret: str):
    cleaned = redact(line)

    assert secret not in cleaned
    assert "***" in cleaned


def test_ordinary_text_is_left_alone():
    for text in ("Rendered clip 12 in 4.2 s with h264_nvenc",
                 "the password to success is practice",
                 "key moments: 4, token count 812, bearer of bad news",
                 "GET /api/clips?q=keyboard&sort=score"):
        assert redact(text) == text


def test_the_log_file_never_contains_a_credential(tmp_path: Path):
    setup_logging(tmp_path, "test", "INFO")
    log = logging.getLogger("clipforge.test")
    try:
        log.info("request failed: Authorization: Bearer abcdefghijklmnop123456",
                 extra={"body": '{"api_key": "sk-secretsecretsecret12"}'})
        try:
            raise RuntimeError("upstream said: access_token=ya29.TopSecretTokenValue")
        except RuntimeError:
            log.error("call failed", exc_info=True)
    finally:
        for handler in list(logging.getLogger().handlers):       # release the file so the folder can be removed
            handler.close()
            logging.getLogger().removeHandler(handler)

    written = (tmp_path / "test.log").read_text(encoding="utf-8")
    lines = [json.loads(line) for line in written.splitlines()]
    assert [entry["level"] for entry in lines] == ["INFO", "ERROR"]
    for secret in ("abcdefghijklmnop123456", "sk-secretsecretsecret12", "ya29.TopSecretTokenValue"):
        assert secret not in written
