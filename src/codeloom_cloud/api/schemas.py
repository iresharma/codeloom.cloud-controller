from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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


class ProjectCreate(BaseModel):
    full_name: str = Field(min_length=3, max_length=255)
    default_branch: str | None = None


class ProjectOut(BaseModel):
    id: str
    full_name: str
    owner: str
    repo: str
    default_branch: str
    created_at: datetime


class SessionOut(BaseModel):
    id: str
    project_id: str
    status: str
    repo: str
    branch: str
    engine_session_id: str | None
    error: str | None
    created_at: datetime
    stopped_at: datetime | None
