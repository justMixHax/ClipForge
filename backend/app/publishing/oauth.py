"""Signing in through the platforms' own pages (OAuth 2.0 with PKCE). ClipForge never sees your password: the
platform sends back a one-time code, which the backend exchanges for tokens and stores encrypted."""
from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from dataclasses import dataclass

LIFETIME = 600.0  # a sign-in has ten minutes to come back


@dataclass
class Pending:
    platform: str
    verifier: str
    redirect_uri: str
    created: float


_pending: dict[str, Pending] = {}
_lock = threading.Lock()


def new_verifier() -> str:
    """64 characters from A-Z a-z 0-9 - _ (inside the 43-128 the standard allows)."""
    return secrets.token_urlsafe(48)


def challenge_s256(verifier: str) -> str:
    """The standard PKCE challenge: base64url(SHA-256(verifier)) without padding (Google)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def challenge_hex(verifier: str) -> str:
    """TikTok's desktop sign-in wants the SHA-256 as hex instead (its documentation says so explicitly)."""
    return hashlib.sha256(verifier.encode("ascii")).hexdigest()


def begin(platform: str, redirect_uri: str) -> tuple[str, str]:
    """A fresh (state, verifier) for one sign-in. The state comes back with the code and is single-use."""
    state = secrets.token_urlsafe(24)
    verifier = new_verifier()
    now = time.time()
    with _lock:
        for key in [k for k, p in _pending.items() if now - p.created > LIFETIME]:
            del _pending[key]
        _pending[state] = Pending(platform, verifier, redirect_uri, now)
    return state, verifier


def finish(state: str | None) -> Pending | None:
    """The sign-in this state belongs to, once. None when it is unknown, used already or too old."""
    with _lock:
        pending = _pending.pop(state or "", None)
    if pending is None or time.time() - pending.created > LIFETIME:
        return None
    return pending
