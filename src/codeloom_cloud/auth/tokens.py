from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
from fastapi import HTTPException
from jwt import InvalidTokenError

from codeloom_cloud.config import Settings
from codeloom_cloud.http_errors import SESSION_INVALID, http_error


def issue_session_token(settings: Settings, user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(days=14),
    }
    return jwt.encode(payload, settings.session_secret, algorithm="HS256")


def read_session_token(settings: Settings, token: str) -> str:
    try:
        payload = jwt.decode(token, settings.session_secret, algorithms=["HS256"])
    except InvalidTokenError as exc:
        raise http_error(401, SESSION_INVALID, "invalid token") from exc
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise http_error(401, SESSION_INVALID, "invalid token")
    return subject


def issue_oauth_state(settings: Settings) -> str:
    payload = {
        "purpose": "oauth",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=10),
    }
    return jwt.encode(payload, settings.session_secret, algorithm="HS256")


def read_oauth_state(settings: Settings, state: str) -> None:
    try:
        payload = jwt.decode(state, settings.session_secret, algorithms=["HS256"])
    except InvalidTokenError as exc:
        raise HTTPException(status_code=400, detail="invalid oauth state") from exc
    if payload.get("purpose") != "oauth":
        raise HTTPException(status_code=400, detail="invalid oauth state")
