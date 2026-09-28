from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from codeloom_cloud.api.schemas import RepoOut
from codeloom_cloud.auth.deps import get_current_user
from codeloom_cloud.auth.github import GitHubError
from codeloom_cloud.crypto import TokenError, decrypt_token
from codeloom_cloud.models import User

router = APIRouter(prefix="/github", tags=["github"])


@router.get("/repos", response_model=list[RepoOut])
async def list_repos(
    request: Request,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=30, ge=1, le=100),
    user: User = Depends(get_current_user),
) -> list[RepoOut]:
    try:
        token = decrypt_token(user.access_token_encrypted, request.app.state.settings)
    except TokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
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
