from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from codeloom_cloud.api.github import github_access_token
from codeloom_cloud.api.schemas import (
    ProjectCreate,
    ProjectOut,
    ProjectOverviewOut,
    PullFileOut,
    PullPreviewOut,
)
from codeloom_cloud.auth.deps import get_current_user
from codeloom_cloud.auth.github import GitHubError
from codeloom_cloud.db import get_db
from codeloom_cloud.models import Project, SessionRecord, User
from codeloom_cloud.sandbox.images import runtime_for_language

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["projects"])

_FULL_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def parse_full_name(value: str) -> tuple[str, str]:
    if not _FULL_NAME.fullmatch(value):
        raise HTTPException(status_code=422, detail="full_name must be owner/repo")
    owner, repo = value.split("/", 1)
    return owner, repo


def owned_project(db: Session, user: User, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None or project.user_id != user.id:
        raise HTTPException(status_code=404, detail="project not found")
    return project


def project_out(project: Project) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        full_name=f"{project.owner}/{project.repo}",
        owner=project.owner,
        repo=project.repo,
        default_branch=project.default_branch,
        runtime=project.runtime,
        created_at=project.created_at,
    )


@router.get("", response_model=list[ProjectOut])
def list_projects(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ProjectOut]:
    rows = (
        db.query(Project)
        .filter(Project.user_id == user.id)
        .order_by(Project.created_at.desc())
        .all()
    )
    return [project_out(row) for row in rows]


@router.post("", response_model=ProjectOut, status_code=201)
async def create_project(
    body: ProjectCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectOut:
    owner, repo = parse_full_name(body.full_name)
    try:
        token = await github_access_token(request, user, db)
        remote = await request.app.state.github.get_repo(token, f"{owner}/{repo}")
    except GitHubError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if remote is None:
        raise HTTPException(status_code=404, detail="repository not found or not accessible")
    branch = body.default_branch or remote.default_branch
    project = Project(
        id=uuid4().hex,
        user_id=user.id,
        name=remote.name or repo,
        owner=owner,
        repo=repo,
        default_branch=branch,
        runtime=runtime_for_language(remote.language),
        created_at=datetime.now(timezone.utc),
    )
    db.add(project)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="project already exists") from exc
    db.refresh(project)
    return project_out(project)


@router.get("/{project_id}/overview", response_model=ProjectOverviewOut)
async def project_overview(
    project_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectOverviewOut:
    project = owned_project(db, user, project_id)
    token = await github_access_token(request, user, db)
    full_name = f"{project.owner}/{project.repo}"
    github = request.app.state.github
    try:
        remote, issues, pulls, contributors = await asyncio.gather(
            github.get_repo(token, full_name),
            github.list_issues(token, full_name),
            github.list_pulls(token, full_name),
            github.list_contributors(token, full_name),
        )
    except GitHubError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if remote is None:
        raise HTTPException(status_code=404, detail="repository not found or not accessible")
    return ProjectOverviewOut.from_github(remote, issues, pulls, contributors)


@router.get("/{project_id}/pulls/{number}", response_model=PullPreviewOut)
async def project_pull(
    project_id: str,
    number: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PullPreviewOut:
    project = owned_project(db, user, project_id)
    token = await github_access_token(request, user, db)
    full_name = f"{project.owner}/{project.repo}"
    github = request.app.state.github
    try:
        pull, files = await asyncio.gather(
            github.get_pull(token, full_name, number),
            github.list_pull_files(token, full_name, number),
        )
    except GitHubError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if pull is None:
        raise HTTPException(status_code=404, detail="pull request not found")
    return PullPreviewOut(
        number=pull.number,
        title=pull.title,
        body=pull.body,
        state=pull.state,
        draft=pull.draft,
        html_url=pull.html_url,
        user=pull.user,
        base=pull.base,
        head=pull.head,
        additions=pull.additions,
        deletions=pull.deletions,
        changed_files=pull.changed_files,
        commits=pull.commits,
        files=[
            PullFileOut(
                filename=item.filename,
                status=item.status,
                additions=item.additions,
                deletions=item.deletions,
                patch=item.patch,
            )
            for item in files
        ],
    )


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectOut:
    return project_out(owned_project(db, user, project_id))


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    project = owned_project(db, user, project_id)
    project_id = project.id
    live = (
        db.query(SessionRecord)
        .filter(
            SessionRecord.project_id == project_id,
            SessionRecord.status.in_(("provisioning", "ready")),
        )
        .all()
    )
    session_ids = [row.id for row in live]
    db.commit()
    for session_id in session_ids:
        try:
            await request.app.state.manager.stop(session_id, user.id)
        except KeyError:
            continue
    db.query(SessionRecord).filter(SessionRecord.project_id == project_id).delete(
        synchronize_session=False
    )
    project = db.get(Project, project_id)
    if project is not None:
        db.delete(project)
    db.commit()
    store = getattr(request.app.state, "memory_store", None)
    if store is not None:
        try:
            store.delete(project_id)
        except Exception:
            logger.exception("memory delete failed for project %s", project_id)
    return Response(status_code=204)
