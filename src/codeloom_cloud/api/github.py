from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from codeloom_cloud.api.schemas import RepoOut
from codeloom_cloud.auth.credentials import ensure_access_token
from codeloom_cloud.auth.deps import get_current_user
from codeloom_cloud.auth.github import GitHubAuthExpired, GitHubError
from codeloom_cloud.crypto import TokenError
from codeloom_cloud.db import get_db
from codeloom_cloud.http_errors import GITHUB_AUTH_EXPIRED, http_error
from codeloom_cloud.models import User

router = APIRouter(prefix="/github", tags=["github"])


async def github_access_token(request: Request, user: User, db) -> str:
    try:
        return await ensure_access_token(
            user, request.app.state.settings, request.app.state.github, db
        )
    except TokenError as exc:
        raise http_error(401, GITHUB_AUTH_EXPIRED, str(exc)) from exc
    except GitHubAuthExpired as exc:
        raise http_error(401, GITHUB_AUTH_EXPIRED, str(exc)) from exc
    except GitHubError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/repos", response_model=list[RepoOut])
async def list_repos(
    request: Request,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=30, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[RepoOut]:
    token = await github_access_token(request, user, db)
    try:
        repos = await request.app.state.github.list_repos(token, page, per_page)
    except GitHubError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return [
        RepoOut(
            full_name=repo.full_name,
            owner=repo.owner,
            name=repo.name,
            default_branch=repo.default_branch,
            private=repo.private,
            description=repo.description,
            language=repo.language,
        )
        for repo in repos
    ]
