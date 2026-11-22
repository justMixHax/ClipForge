"""What every platform adapter shares: errors, tokens, what to post, what came of it, and a careful HTTP client
(timeouts, messages a person can act on, and no tokens in error messages, URLs or logs)."""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from ..errors import ClipForgeError, JobCancelled

# httpx logs every request address at INFO. Upload addresses carry one-time signatures: keep them out of the logs.
logging.getLogger("httpx").setLevel(logging.WARNING)

USER_AGENT = "ClipForge/0.1 (personal clipping studio)"
BLOCK = 1024 * 1024  # bytes read per step while streaming a file


class PublishError(ClipForgeError):
    """A post could not be made. retry: trying again later may work. reconnect: the sign-in must be renewed."""

    def __init__(self, message: str, *, hint: str = "", retry: bool = False, reconnect: bool = False) -> None:
        super().__init__(message, hint=hint)
        self.retry = retry
        self.reconnect = reconnect


@dataclass
class Tokens:
    access_token: str
    refresh_token: str = ""
    expires_at: float = 0.0          # unix time the access token runs out; 0 = not known
    refresh_expires_at: float = 0.0  # unix time the sign-in itself runs out; 0 = not known
    scopes: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> Tokens:
        data = json.loads(text)
        return cls(**{name: data[name] for name in cls.__dataclass_fields__ if name in data})

    def expiring(self, within: float = 300.0) -> bool:
        return bool(self.expires_at) and self.expires_at - time.time() < within


@dataclass
class Profile:
    """Who signed in: the account a post will go to."""
    external_id: str
    name: str
    handle: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Draft:
    """What to post: the rendered clip and the text, as you approved them."""
    file: Path
    title: str
    caption: str
    hashtags: list[str]
    privacy: str = ""
    publish_at: datetime | None = None
    options: dict[str, Any] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return self.file.stat().st_size


@dataclass
class Outcome:
    """What the platform confirmed. status is one of models.POST_STATUSES."""
    status: str
    external_id: str = ""
    url: str = ""
    detail: str = ""
    check_again_in: float | None = None  # seconds, while the platform is still working on it


ProgressFn = Callable[[float], None]
CancelFn = Callable[[], bool]
RememberFn = Callable[[dict[str, Any]], None]


def file_part(path: Path, start: int, end: int, on_bytes: Callable[[int], None] | None = None,
              should_cancel: CancelFn | None = None) -> Iterator[bytes]:
    """Bytes start..end-1 of a file, a megabyte at a time, stopping at once when cancelled."""
    with open(path, "rb") as source:
        source.seek(start)
        left = end - start
        while left > 0:
            if should_cancel is not None and should_cancel():
                raise JobCancelled()
            block = source.read(min(BLOCK, left))
            if not block:
                break
            left -= len(block)
            if on_bytes is not None:
                on_bytes(len(block))
            yield block


def pause(seconds: float, should_cancel: CancelFn | None = None) -> None:
    """Wait, but stop at once when the job is cancelled."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if should_cancel is not None and should_cancel():
            raise JobCancelled()
        time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))


def platform_message(response: httpx.Response) -> str:
    """The platform's own words about an error, short. Never the request (which carries the token)."""
    try:
        data = response.json()
    except ValueError:
        return response.text.strip()[:200] if response.headers.get("content-type", "").startswith("text/plain") else ""
    if not isinstance(data, dict):
        return ""
    error = data.get("error")
    if isinstance(error, dict):
        text = error.get("message") or error.get("error_user_msg") or error.get("code") or ""
    else:
        text = data.get("error_description") or error or data.get("message") or ""
    return str(text).strip()[:300]


class Http:
    """A small HTTP client for one platform. Every failure becomes a PublishError with a message to act on."""

    def __init__(self, label: str, *, timeout: float = 60.0, client: httpx.Client | None = None) -> None:
        self.label = label
        self.client = client or httpx.Client(timeout=httpx.Timeout(timeout, connect=15.0), follow_redirects=False,
                                             headers={"User-Agent": USER_AGENT})

    def request(self, method: str, url: str, *, expect: tuple[int, ...] = (200, 201), **kwargs: Any) -> httpx.Response:
        try:
            response = self.client.request(method, url, **kwargs)
        except JobCancelled:
            raise
        except httpx.TimeoutException as exc:
            raise PublishError(f"{self.label} did not answer in time.", retry=True) from exc
        except httpx.TransportError as exc:
            raise PublishError(f"Could not reach {self.label} ({type(exc).__name__}).",
                               hint="Check the internet connection.", retry=True) from exc
        if response.status_code in expect:
            return response
        raise self.error_for(response)

    def json(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        response = self.request(method, url, **kwargs)
        try:
            data = response.json()
        except ValueError as exc:
            raise PublishError(f"{self.label} sent an answer ClipForge could not read.", retry=True) from exc
        return data if isinstance(data, dict) else {}

    def error_for(self, response: httpx.Response) -> PublishError:
        status = response.status_code
        said = platform_message(response)
        suffix = f": {said}" if said else ""
        if status == 401:
            return PublishError(f"{self.label} no longer accepts this sign-in{suffix}.",
                                hint="Reconnect the account under Publishing → Accounts.", reconnect=True)
        if status == 429:
            return PublishError(f"{self.label} says there have been too many requests{suffix}.",
                                hint="ClipForge tries again later.", retry=True)
        if status >= 500:
            return PublishError(f"{self.label} had a problem on its side ({status}){suffix}.", retry=True)
        return PublishError(f"{self.label} refused the request ({status}){suffix}.")

    def close(self) -> None:
        self.client.close()
