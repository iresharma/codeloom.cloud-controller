from __future__ import annotations

import time
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from codeloom_cloud.auth.github import GitHubRepo
from tests.github_fake import FakeGitHub


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(
    client: TestClient,
    github: FakeGitHub,
    *,
    login_name: str = "octocat",
    github_id: int = 1,
    access_token: str | None = None,
) -> tuple[str, str]:
    access = access_token or f"gho_{login_name}"
    github.add_user(access, github_id=github_id, login=login_name, name=login_name)
    start = client.get("/auth/github/login", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    callback = client.get(
        "/auth/github/callback",
        params={"code": access, "state": state},
        follow_redirects=False,
    )
    assert callback.status_code in (302, 307)
    fragment = urlparse(callback.headers["location"]).fragment
    session_token = parse_qs(fragment)["token"][0]
    return session_token, access


def allow_repo(
    github: FakeGitHub,
    access_token: str,
    full_name: str = "octocat/hello",
    branch: str = "main",
    private: bool = False,
) -> GitHubRepo:
    owner, name = full_name.split("/", 1)
    repo = GitHubRepo(
        full_name=full_name,
        owner=owner,
        name=name,
        default_branch=branch,
        private=private,
        description=None,
    )
    github.allow(access_token, repo)
    return repo


def wait_until_settled(client: TestClient, token: str, session_id: str) -> dict:
    last = None
    for _ in range(100):
        response = client.get(f"/sessions/{session_id}", headers=auth_header(token))
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] != "provisioning":
            return last
        time.sleep(0.05)
    raise AssertionError(last)
