"""Encryption for stored credentials (API keys, OAuth tokens).

Values are encrypted with a random Fernet key (AES-128-CBC + HMAC). The key itself is kept in the data
folder, and on Windows it is wrapped with DPAPI first, which ties it to this Windows user account: a
copied database plus key file cannot be decrypted on another PC or by another user.
"""
from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

_DPAPI_PREFIX = b"DPAPI1:"


class SecretError(RuntimeError):
    pass


def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                   wintypes.DWORD, ctypes.POINTER(DataBlob)]
    fn.restype = wintypes.BOOL

    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = DataBlob()
    if not fn(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise SecretError("Windows could not protect/unprotect the encryption key "
                          "(was the data folder copied from another PC or user account?)")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def _wrap(key: bytes) -> bytes:
    if sys.platform == "win32":
        return _DPAPI_PREFIX + base64.b64encode(_dpapi(key, protect=True))
    return key


def _unwrap(stored: bytes) -> bytes:
    stored = stored.strip()
    if stored.startswith(_DPAPI_PREFIX):
        if sys.platform != "win32":
            raise SecretError("this key file was created on Windows and can only be opened there")
        return _dpapi(base64.b64decode(stored[len(_DPAPI_PREFIX):]), protect=False)
    return stored


class SecretBox:
    def __init__(self, key_path: Path) -> None:
        self._key_path = key_path
        self._raw_key = self._load_or_create()
        self._fernet = Fernet(self._raw_key)

    def _load_or_create(self) -> bytes:
        if self._key_path.exists():
            return _unwrap(self._key_path.read_bytes())
        key = Fernet.generate_key()
        self._key_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._key_path.with_suffix(".tmp")
        tmp.write_bytes(_wrap(key))
        if sys.platform != "win32":
            os.chmod(tmp, 0o600)
        os.replace(tmp, self._key_path)
        return key

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode("utf-8"))

    def decrypt(self, token: bytes) -> str:
        try:
            return self._fernet.decrypt(token).decode("utf-8")
        except InvalidToken as exc:
            raise SecretError("stored credential could not be decrypted with this PC's key") from exc
