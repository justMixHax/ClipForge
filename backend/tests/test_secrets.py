from __future__ import annotations

from pathlib import Path

import pytest

from app.security.secrets import SecretBox, SecretError


def test_round_trip(tmp_path: Path):
    box = SecretBox(tmp_path / "secret.key")
    token = box.encrypt("ya29.refresh-token-value")
    assert box.decrypt(token) == "ya29.refresh-token-value"


def test_ciphertext_does_not_contain_the_plaintext(tmp_path: Path):
    box = SecretBox(tmp_path / "secret.key")
    assert b"super-secret-api-key" not in box.encrypt("super-secret-api-key")


def test_key_is_created_once_and_reused_by_a_new_instance(tmp_path: Path):
    key_path = tmp_path / "secret.key"
    token = SecretBox(key_path).encrypt("hello")
    assert SecretBox(key_path).decrypt(token) == "hello"


def test_a_different_key_cannot_decrypt(tmp_path: Path):
    token = SecretBox(tmp_path / "a.key").encrypt("hello")
    with pytest.raises(SecretError):
        SecretBox(tmp_path / "b.key").decrypt(token)


def test_key_file_on_disk_is_not_the_raw_key(tmp_path: Path):
    """On Windows the key is wrapped with the user's DPAPI key, so copying the file alone is useless."""
    import sys

    key_path = tmp_path / "secret.key"
    box = SecretBox(key_path)
    stored = key_path.read_bytes()
    if sys.platform == "win32":
        assert box._raw_key not in stored
    else:
        assert stored.strip() == box._raw_key
