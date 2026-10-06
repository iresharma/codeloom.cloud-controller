from __future__ import annotations

from codeloom_cloud.memory_store import FileMemoryStore
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


def test_project_overview_lists_issues_pulls_and_contributors(client, github):
    from codeloom_cloud.auth.github import GitHubContributor, GitHubRepo, GitHubWorkItem

    token, access = login(client, github)
    allow_repo(github, access, language="TypeScript")
    github.repos[access][0] = GitHubRepo(
        full_name="octocat/hello",
        owner="octocat",
        name="hello",
        default_branch="main",
        private=False,
        description="A dry run.",
        language="TypeScript",
        stars=12,
        forks=3,
        open_issues=4,
        html_url="https://github.com/octocat/hello",
    )
    github.issues[(access, "octocat/hello")] = [
        GitHubWorkItem(4, "Fix the loom", "https://github.com/octocat/hello/issues/4", "hubot", "2026-10-01T00:00:00Z"),
    ]
    github.pulls[(access, "octocat/hello")] = [
        GitHubWorkItem(9, "Add the warp", "https://github.com/octocat/hello/pull/9", "octocat", "2026-10-02T00:00:00Z"),
    ]
    github.contributors[(access, "octocat/hello")] = [
        GitHubContributor("octocat", "https://example.com/a.png", 20),
    ]
    created = client.post(
        "/projects",
        headers=auth_header(token),
        json={"full_name": "octocat/hello"},
    )
    overview = client.get(
        f"/projects/{created.json()['id']}/overview",
        headers=auth_header(token),
    )
    assert overview.status_code == 200
    body = overview.json()
    assert body["language"] == "TypeScript"
    assert body["stars"] == 12
    assert body["issues"][0]["number"] == 4
    assert body["pulls"][0]["title"] == "Add the warp"
    assert body["contributors"][0]["login"] == "octocat"


def test_project_pull_preview(client, github):
    from codeloom_cloud.auth.github import GitHubPull, GitHubPullFile

    token, access = login(client, github)
    allow_repo(github, access)
    created = client.post(
        "/projects",
        headers=auth_header(token),
        json={"full_name": "octocat/hello"},
    )
    github.pull_details[(access, "octocat/hello", 9)] = GitHubPull(
        number=9,
        title="Add the warp",
        body="Opens the loom.",
        state="open",
        draft=False,
        html_url="https://github.com/octocat/hello/pull/9",
        user="octocat",
        base="main",
        head="warp",
        additions=12,
        deletions=2,
        changed_files=1,
        commits=1,
    )
    github.pull_files[(access, "octocat/hello", 9)] = [
        GitHubPullFile("loom.py", "modified", 12, 2, "@@\n-old\n+new"),
    ]
    preview = client.get(
        f"/projects/{created.json()['id']}/pulls/9",
        headers=auth_header(token),
    )
    assert preview.status_code == 200
    body = preview.json()
    assert body["title"] == "Add the warp"
    assert body["head"] == "warp"
    assert body["files"][0]["filename"] == "loom.py"
    missing = client.get(
        f"/projects/{created.json()['id']}/pulls/3",
        headers=auth_header(token),
    )
    assert missing.status_code == 404


def test_invalid_full_name_is_rejected(client, github):
    token, _access = login(client, github)
    response = client.post(
        "/projects",
        headers=auth_header(token),
        json={"full_name": "not a repo"},
    )
    assert response.status_code == 422


def test_delete_project_removes_stored_memory(client, github, settings):
    token, access = login(client, github)
    allow_repo(github, access, full_name="octocat/gone")
    project = client.post(
        "/projects",
        headers=auth_header(token),
        json={"full_name": "octocat/gone"},
    ).json()
    store = FileMemoryStore(settings.data_dir / "memory")
    store.save(project["id"], {"files": {}, "engineering": [{"text": "x"}]})
    assert store.load(project["id"]) is not None

    response = client.delete(f"/projects/{project['id']}", headers=auth_header(token))
    assert response.status_code == 204
    assert store.load(project["id"]) is None
