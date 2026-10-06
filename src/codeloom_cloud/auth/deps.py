from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from codeloom_cloud.auth.tokens import read_session_token
from codeloom_cloud.db import get_db
from codeloom_cloud.http_errors import SESSION_INVALID, http_error
from codeloom_cloud.models import User


def get_current_user(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise http_error(401, SESSION_INVALID, "missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    user_id = read_session_token(request.app.state.settings, token)
    user = db.get(User, user_id)
    if user is None:
        raise http_error(401, SESSION_INVALID, "invalid token")
    return user
