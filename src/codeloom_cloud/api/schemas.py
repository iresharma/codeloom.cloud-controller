from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    login: str
    name: str | None
    avatar_url: str | None
    github_refreshable: bool = False
    github_access_expires_at: datetime | None = None

    @field_serializer("github_access_expires_at")
    def _github_access_expires_at(self, value: datetime | None) -> str | None:
        return utc_z(value)


class RepoOut(BaseModel):
    full_name: str
    owner: str
    name: str
    default_branch: str
    private: bool
    description: str | None = None
    language: str | None = None


class WorkItemOut(BaseModel):
    number: int
    title: str
    html_url: str
    user: str
    updated_at: str | None = None


class ContributorOut(BaseModel):
    login: str
    avatar_url: str | None = None
    contributions: int


class ProjectOverviewOut(BaseModel):
    full_name: str
    description: str | None = None
    language: str | None = None
    default_branch: str
    private: bool
    html_url: str
    stars: int
    forks: int
    open_issues: int
    pushed_at: str | None = None
    issues: list[WorkItemOut]
    pulls: list[WorkItemOut]
    contributors: list[ContributorOut]

    @classmethod
    def from_github(cls, repo, issues, pulls, contributors) -> "ProjectOverviewOut":
        return cls(
            full_name=repo.full_name,
            description=repo.description,
            language=repo.language,
            default_branch=repo.default_branch,
            private=repo.private,
            html_url=repo.html_url,
            stars=repo.stars,
            forks=repo.forks,
            open_issues=repo.open_issues,
            pushed_at=repo.pushed_at,
            issues=[
                WorkItemOut(
                    number=item.number,
                    title=item.title,
                    html_url=item.html_url,
                    user=item.user,
                    updated_at=item.updated_at,
                )
                for item in issues
            ],
            pulls=[
                WorkItemOut(
                    number=item.number,
                    title=item.title,
                    html_url=item.html_url,
                    user=item.user,
                    updated_at=item.updated_at,
                )
                for item in pulls
            ],
            contributors=[
                ContributorOut(
                    login=person.login,
                    avatar_url=person.avatar_url,
                    contributions=person.contributions,
                )
                for person in contributors
            ],
        )


class PullFileOut(BaseModel):
    filename: str
    status: str
    additions: int
    deletions: int
    patch: str


class PullPreviewOut(BaseModel):
    number: int
    title: str
    body: str
    state: str
    draft: bool
    html_url: str
    user: str
    base: str
    head: str
    additions: int
    deletions: int
    changed_files: int
    commits: int
    files: list[PullFileOut]


class ProjectCreate(BaseModel):
    full_name: str = Field(min_length=3, max_length=255)
    default_branch: str | None = None


def utc_z(value: datetime | None) -> str | None:
    """SQLite returns naive UTC. Emit Z so browsers do not treat it as local time."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    iso = value.isoformat(timespec="seconds")
    if iso.endswith("+00:00"):
        return f"{iso[:-6]}Z"
    return iso


class ProjectOut(BaseModel):
    id: str
    full_name: str
    owner: str
    repo: str
    default_branch: str
    runtime: str
    created_at: datetime

    @field_serializer("created_at")
    def _created_at(self, value: datetime) -> str:
        return utc_z(value) or ""


class SessionTitleIn(BaseModel):
    title: str = Field(min_length=1, max_length=4000)


class SessionArchiveIn(BaseModel):
    stats: dict | None = None
    items: list[dict] = Field(default_factory=list)


class SessionOut(BaseModel):
    id: str
    project_id: str
    status: str
    repo: str
    branch: str
    engine_session_id: str | None
    error: str | None
    title: str | None = None
    created_at: datetime
    stopped_at: datetime | None

    @field_serializer("created_at", "stopped_at")
    def _stamps(self, value: datetime | None) -> str | None:
        return utc_z(value)
