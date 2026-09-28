from __future__ import annotations

from tests.helpers import allow_repo, auth_header, login


def test_create_project_checks_github_access(client, github):
    token, access = login(client, github)
    denied = client.post(
        "/projects",
        headers=auth_header(token),
        json={"full_name": "octocat/missing"},
    )
    assert denied.status_code == 404

    allow_repo(github, access, private=True)
    created = client.post(
        "/projects",
        headers=auth_header(token),
        json={"full_name": "octocat/hello"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["full_name"] == "octocat/hello"
    assert body["default_branch"] == "main"
    assert body["owner"] == "octocat"
    assert body["runtime"] == "python"

    again = client.post(
        "/projects",
        headers=auth_header(token),
        json={"full_name": "octocat/hello"},
    )
    assert again.status_code == 409

    listed = client.get("/projects", headers=auth_header(token))
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [body["id"]]


def test_projects_are_scoped_to_the_user(client, github):
    token, access = login(client, github)
    allow_repo(github, access)
    created = client.post(
        "/projects",
        headers=auth_header(token),
        json={"full_name": "octocat/hello", "default_branch": "dev"},
    )
    project_id = created.json()["id"]
    assert created.json()["default_branch"] == "dev"

    other, _other_access = login(
        client,
        github,
        login_name="hubot",
        github_id=2,
        access_token="gho_hubot",
    )
    missing = client.get(f"/projects/{project_id}", headers=auth_header(other))
    assert missing.status_code == 404
    assert client.get("/projects", headers=auth_header(other)).json() == []


def test_repo_list_uses_the_stored_token(client, github):
    token, access = login(client, github)
    allow_repo(github, access, full_name="octocat/hello")
    allow_repo(github, access, full_name="octocat/other", branch="trunk")
    response = client.get("/github/repos", headers=auth_header(token))
    assert response.status_code == 200
    names = [item["full_name"] for item in response.json()]
    assert names == ["octocat/hello", "octocat/other"]


def test_invalid_full_name_is_rejected(client, github):
    token, _access = login(client, github)
    response = client.post(
        "/projects",
        headers=auth_header(token),
        json={"full_name": "not a repo"},
    )
    assert response.status_code == 422
