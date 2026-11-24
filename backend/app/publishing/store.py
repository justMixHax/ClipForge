"""Your developer-app credentials and connected accounts. Both are kept encrypted (security/secrets.py) and never
returned by the API; only whether an app is set up, its public ID, and the account's name are shown."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..errors import ClipForgeError
from ..models import Account
from ..security.secrets import SecretError
from ..services import settings_store
from .base import Profile, PublishError, Tokens
from .instagram import Instagram
from .tiktok import TikTok
from .youtube import YouTube

ADAPTERS: dict[str, Any] = {"youtube": YouTube, "tiktok": TikTok, "instagram": Instagram}
LABELS = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram"}
OVERRIDES: dict[str, dict[str, str]] = {}  # the tests point the adapters at local fakes


def check_platform(platform: str) -> None:
    if platform not in ADAPTERS:
        raise ClipForgeError(f"Unknown platform: {platform}")


def _app_secret(platform: str) -> str:
    return f"publish.app.{platform}"


def _account_secret(account_id: int) -> str:
    return f"publish.account.{account_id}"


# ------------------------------------------------------------------------------------------ developer apps
def save_app(session: Session, platform: str, client_id: str, client_secret: str) -> None:
    check_platform(platform)
    client_id, client_secret = client_id.strip(), client_secret.strip()
    if not client_id or not client_secret:
        raise ClipForgeError("Both the ID and the secret are needed.")
    if len(client_id) > 300 or len(client_secret) > 300 or any(c.isspace() for c in client_id + client_secret):
        raise ClipForgeError("That does not look like an app ID and secret (no spaces, at most 300 characters).")
    settings_store.put_secret(session, _app_secret(platform),
                              json.dumps({"client_id": client_id, "client_secret": client_secret}))


def remove_app(session: Session, platform: str) -> None:
    check_platform(platform)
    settings_store.put_secret(session, _app_secret(platform), "")


def app_credentials(session: Session, platform: str) -> dict[str, str] | None:
    raw = settings_store.read_secret(session, _app_secret(platform))
    return json.loads(raw) if raw else None


def app_summary(session: Session, platform: str) -> dict[str, Any]:
    """Safe to show: whether the app is set up, and its public ID. Never the secret. Credentials this PC cannot
    read (the data folder came from another PC or Windows account) count as not set up: they have to be entered
    again, and the page has to open for that."""
    try:
        creds = app_credentials(session, platform)
    except SecretError:
        creds = None
    return {"configured": creds is not None, "client_id": creds["client_id"] if creds else ""}


def adapter(session: Session, platform: str) -> Any:
    check_platform(platform)
    creds = app_credentials(session, platform)
    if creds is None:
        raise ClipForgeError(f"Add your {LABELS[platform]} app's ID and secret first.",
                             hint="Publishing → Accounts shows where to get them.")
    return ADAPTERS[platform](creds["client_id"], creds["client_secret"], endpoints=OVERRIDES.get(platform))


# ------------------------------------------------------------------------------------------------ accounts
def save_tokens(session: Session, account_id: int, tokens: Tokens) -> None:
    settings_store.put_secret(session, _account_secret(account_id), tokens.to_json())


def load_tokens(session: Session, account_id: int) -> Tokens | None:
    raw = settings_store.read_secret(session, _account_secret(account_id))
    return Tokens.from_json(raw) if raw else None


def forget_tokens(session: Session, account_id: int) -> None:
    settings_store.put_secret(session, _account_secret(account_id), "")


def _when(timestamp: float) -> datetime | None:
    return datetime.fromtimestamp(timestamp, timezone.utc) if timestamp else None


def upsert_account(session: Session, platform: str, profile: Profile, tokens: Tokens) -> Account:
    """Add the account, or bring it up to date when the same account connects again."""
    account = session.query(Account).filter(Account.platform == platform,
                                            Account.external_id == profile.external_id).one_or_none()
    if account is None:
        account = Account(platform=platform, external_id=profile.external_id)
        session.add(account)
    account.name = (profile.name or LABELS[platform])[:200]
    account.handle = profile.handle[:200]
    account.extra = dict(profile.extra)
    account.status, account.detail = "connected", ""
    account.scopes = list(tokens.scopes)
    account.reconnect_by = _when(tokens.refresh_expires_at)
    session.flush()
    save_tokens(session, account.id, tokens)
    session.commit()
    return account


def mark_reconnect(session: Session, account: Account, why: str) -> None:
    account.status = "needs_reconnect"
    account.detail = why[:500]
    session.commit()


def fresh_tokens(session: Session, account: Account, platform_adapter: Any) -> Tokens:
    """The account's tokens, renewed first when they are about to run out. A sign-in that cannot be renewed
    marks the account as needing to reconnect."""
    tokens = load_tokens(session, account.id)
    if tokens is None:
        mark_reconnect(session, account, "No sign-in is stored for this account.")
        raise PublishError(f"{account.name} has no stored sign-in.", hint="Reconnect it under Publishing → Accounts.",
                           reconnect=True)
    if not tokens.expiring():
        return tokens
    if not tokens.refresh_token:
        if tokens.expires_at and tokens.expires_at <= time.time():
            mark_reconnect(session, account, "The sign-in ran out.")
            raise PublishError(f"The sign-in for {account.name} ran out.", hint="Reconnect it under Publishing → "
                                                                                 "Accounts.", reconnect=True)
        return tokens
    try:
        renewed = platform_adapter.refresh(tokens)
    except PublishError as exc:
        if exc.reconnect:
            mark_reconnect(session, account, exc.message)
        raise
    save_tokens(session, account.id, renewed)
    if renewed.refresh_expires_at:
        account.reconnect_by = _when(renewed.refresh_expires_at)
    session.commit()
    return renewed
