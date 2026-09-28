from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from codeloom_cloud.api.schemas import UserOut
from codeloom_cloud.auth.credentials import apply_github_grant
from codeloom_cloud.auth.deps import get_current_user
from codeloom_cloud.auth.github import GitHubError
from codeloom_cloud.auth.tokens import issue_oauth_state, issue_session_token, read_oauth_state
from codeloom_cloud.db import get_db
from codeloom_cloud.models import User

router = APIRouter(tags=["auth"])


@router.get("/auth/github/login")
def github_login(request: Request) -> RedirectResponse:
    settings = request.app.state.settings
    if not settings.github_client_id or not settings.github_client_secret:
        raise HTTPException(status_code=500, detail="github oauth is not configured")
    state = issue_oauth_state(settings)
    return RedirectResponse(request.app.state.github.authorize_url(state))


@router.get("/auth/github/callback")
async def github_callback(
    request: Request,
    code: str = "",
    state: str = "",
    db: Session = Depends(get_db),
) -> RedirectResponse:
    settings = request.app.state.settings
    if not code or not state:
        raise HTTPException(status_code=400, detail="missing oauth code or state")
    read_oauth_state(settings, state)
    try:
        grant = await request.app.state.github.exchange_code(code)
        profile = await request.app.state.github.get_user(grant.access_token)
    except GitHubError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    user = db.query(User).filter(User.github_id == profile.id).one_or_none()
    if user is None:
        user = User(
            id=uuid4().hex,
            github_id=profile.id,
            login=profile.login,
            name=profile.name,
            avatar_url=profile.avatar_url,
            access_token_encrypted="",
            created_at=datetime.now(timezone.utc),
        )
        db.add(user)
    else:
        user.login = profile.login
        user.name = profile.name
        user.avatar_url = profile.avatar_url
    apply_github_grant(user, grant, settings)
    db.commit()
    session_token = issue_session_token(settings, user.id)
    target = f"{settings.frontend_origin}/auth/callback#token={quote(session_token)}"
    return RedirectResponse(target)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user
