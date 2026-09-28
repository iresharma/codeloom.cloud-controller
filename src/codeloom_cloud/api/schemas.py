from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    login: str
    name: str | None
    avatar_url: str | None


class RepoOut(BaseModel):
    full_name: str
    owner: str
    name: str
    default_branch: str
    private: bool
    description: str | None = None
    language: str | None = None


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
