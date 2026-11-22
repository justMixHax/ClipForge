"""Exception types shared across the backend.

ClipForgeError carries a message that is safe and useful to show in the browser. Anything else that
escapes a request is logged in full and reported to the browser as a generic server error.
"""
from __future__ import annotations


class ClipForgeError(Exception):
    status_code = 400

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class NotFound(ClipForgeError):
    status_code = 404


class Conflict(ClipForgeError):
    status_code = 409


class Forbidden(ClipForgeError):
    status_code = 403


class UnsupportedType(ClipForgeError):
    status_code = 415


class TooLarge(ClipForgeError):
    status_code = 413


class MediaError(ClipForgeError):
    """The file is not usable media (corrupt, not a video, unsupported)."""

    status_code = 422


class DependencyMissing(ClipForgeError):
    """A required local tool or model is not installed."""

    status_code = 503


class JobCancelled(Exception):
    """Raised inside a job when the user asked to cancel it."""
