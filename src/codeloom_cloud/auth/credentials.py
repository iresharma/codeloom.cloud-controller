from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from codeloom_cloud.auth.github import GitHubAPI, GitHubAuthExpired, GitHubError, GitHubToken
from codeloom_cloud.config import Settings
from codeloom_cloud.crypto import decrypt_token, encrypt_token
from codeloom_cloud.models import User

# Mint a new access token while the current one still covers a typical run.
# GitHub's expiring OAuth tokens last eight hours; a sandbox started near the
# end of that window would otherwise die on the first push.
_REFRESH_SKEW = timedelta(hours=2)


def apply_github_grant(
    user: User,
    grant: GitHubToken,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> None:
    moment = now or datetime.now(timezone.utc)
    user.access_token_encrypted = encrypt_token(grant.access_token, settings)
    if grant.refresh_token:
        user.refresh_token_encrypted = encrypt_token(grant.refresh_token, settings)
    else:
        user.refresh_token_encrypted = None
    if grant.expires_in is None:
        user.access_token_expires_at = None
    else:
        user.access_token_expires_at = moment + timedelta(seconds=grant.expires_in)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _expiring(user: User, now: datetime) -> bool:
    if not user.refresh_token_encrypted:
        return False
    expires = _aware(user.access_token_expires_at)
    if expires is None:
        return False
    return expires <= now + _REFRESH_SKEW


async def ensure_access_token(
    user: User,
    settings: Settings,
    github: GitHubAPI | None,
    db: Session,
) -> str:
    """Return a GitHub access token, refreshing it when the grant is expiring."""
    if github is not None and _expiring(user, datetime.now(timezone.utc)):
        await _rotate(user, settings, github, db)
    token = decrypt_token(user.access_token_encrypted, settings)
    if github is None:
        return token
    try:
        await github.get_user(token)
    except GitHubError as exc:
        if exc.status != 401:
            raise
        if not user.refresh_token_encrypted:
            raise GitHubAuthExpired("GitHub authorization expired. Sign in again.") from exc
        await _rotate(user, settings, github, db)
        token = decrypt_token(user.access_token_encrypted, settings)
    return token


async def _rotate(user: User, settings: Settings, github: GitHubAPI, db: Session) -> None:
    if not user.refresh_token_encrypted:
        raise GitHubAuthExpired("GitHub authorization expired. Sign in again.")
    refresh = decrypt_token(user.refresh_token_encrypted, settings)
    try:
        grant = await github.refresh_access_token(refresh)
    except GitHubError as exc:
        raise GitHubAuthExpired("GitHub authorization expired. Sign in again.") from exc
    apply_github_grant(user, grant, settings)
    db.commit()
