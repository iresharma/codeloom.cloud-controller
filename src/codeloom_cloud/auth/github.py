from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from codeloom_cloud.config import Settings


class GitHubError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class GitHubAuthExpired(GitHubError):
    """The stored GitHub grant can no longer be used or refreshed."""


@dataclass(frozen=True)
class GitHubToken:
    access_token: str
    refresh_token: str | None = None
    expires_in: int | None = None


def token_from_oauth_body(body: dict) -> GitHubToken:
    token = body.get("access_token")
    if not isinstance(token, str) or not token:
        description = body.get("error_description") or "github token exchange failed"
        raise GitHubError(str(description))
    refresh = body.get("refresh_token")
    expires = body.get("expires_in")
    expires_in = expires if isinstance(expires, int) and expires > 0 else None
    return GitHubToken(
        access_token=token,
        refresh_token=refresh if isinstance(refresh, str) and refresh else None,
        expires_in=expires_in,
    )


@dataclass(frozen=True)
class GitHubProfile:
    id: int
    login: str
    name: str | None
    avatar_url: str | None


@dataclass(frozen=True)
class GitHubRepo:
    full_name: str
    owner: str
    name: str
    default_branch: str
    private: bool
    description: str | None
    language: str | None = None
    stars: int = 0
    forks: int = 0
    open_issues: int = 0
    html_url: str = ""
    pushed_at: str | None = None


@dataclass(frozen=True)
class GitHubWorkItem:
    number: int
    title: str
    html_url: str
    user: str
    updated_at: str | None


@dataclass(frozen=True)
class GitHubContributor:
    login: str
    avatar_url: str | None
    contributions: int


@dataclass(frozen=True)
class GitHubPull:
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


@dataclass(frozen=True)
class GitHubPullFile:
    filename: str
    status: str
    additions: int
    deletions: int
    patch: str


class GitHubAPI:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._client = client
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    def authorize_url(self, state: str) -> str:
        query = urlencode(
            {
                "client_id": self.settings.github_client_id,
                "redirect_uri": self.settings.github_oauth_callback_url,
                "scope": "read:user repo",
                "state": state,
            }
        )
        return f"https://github.com/login/oauth/authorize?{query}"

    async def exchange_code(self, code: str) -> GitHubToken:
        response = await self._request(
            "POST",
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": self.settings.github_client_id,
                "client_secret": self.settings.github_client_secret,
                "code": code,
                "redirect_uri": self.settings.github_oauth_callback_url,
            },
            headers={"Accept": "application/json"},
        )
        return token_from_oauth_body(response.json())

    async def refresh_access_token(self, refresh_token: str) -> GitHubToken:
        response = await self._request(
            "POST",
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": self.settings.github_client_id,
                "client_secret": self.settings.github_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            headers={"Accept": "application/json"},
        )
        return token_from_oauth_body(response.json())

    async def get_user(self, token: str) -> GitHubProfile:
        body = await self._get_json("https://api.github.com/user", token)
        return GitHubProfile(
            id=int(body["id"]),
            login=str(body["login"]),
            name=body.get("name"),
            avatar_url=body.get("avatar_url"),
        )

    async def list_repos(self, token: str, page: int, per_page: int) -> list[GitHubRepo]:
        body = await self._get_json(
            "https://api.github.com/user/repos",
            token,
            params={
                "page": page,
                "per_page": per_page,
                "sort": "updated",
                "affiliation": "owner,collaborator,organization_member",
            },
        )
        if not isinstance(body, list):
            raise GitHubError("github repo list was not a list")
        return [_repo(item) for item in body]

    async def get_repo(self, token: str, full_name: str) -> GitHubRepo | None:
        try:
            body = await self._get_json(f"https://api.github.com/repos/{full_name}", token)
        except GitHubError as exc:
            if exc.status == 404:
                return None
            raise
        return _repo(body)

    async def list_issues(self, token: str, full_name: str, per_page: int = 8) -> list[GitHubWorkItem]:
        body = await self._get_json(
            f"https://api.github.com/repos/{full_name}/issues",
            token,
            params={"state": "open", "per_page": 30, "sort": "updated"},
        )
        if not isinstance(body, list):
            raise GitHubError("github issue list was not a list")
        issues = [_work_item(item) for item in body if isinstance(item, dict) and not item.get("pull_request")]
        return issues[:per_page]

    async def list_pulls(self, token: str, full_name: str, per_page: int = 8) -> list[GitHubWorkItem]:
        body = await self._get_json(
            f"https://api.github.com/repos/{full_name}/pulls",
            token,
            params={"state": "open", "per_page": per_page, "sort": "updated"},
        )
        if not isinstance(body, list):
            raise GitHubError("github pull list was not a list")
        return [_work_item(item) for item in body]

    async def list_contributors(
        self, token: str, full_name: str, per_page: int = 8
    ) -> list[GitHubContributor]:
        try:
            body = await self._get_json(
                f"https://api.github.com/repos/{full_name}/contributors",
                token,
                params={"per_page": per_page},
            )
        except GitHubError as exc:
            if exc.status in (204, 403, 404):
                return []
            raise
        if not isinstance(body, list):
            return []
        people: list[GitHubContributor] = []
        for item in body:
            if not isinstance(item, dict) or item.get("type") == "Bot":
                continue
            login = item.get("login")
            if not isinstance(login, str) or not login:
                continue
            people.append(
                GitHubContributor(
                    login=login,
                    avatar_url=item.get("avatar_url"),
                    contributions=int(item.get("contributions") or 0),
                )
            )
        return people

    async def get_pull(self, token: str, full_name: str, number: int) -> GitHubPull | None:
        try:
            body = await self._get_json(
                f"https://api.github.com/repos/{full_name}/pulls/{number}",
                token,
            )
        except GitHubError as exc:
            if exc.status == 404:
                return None
            raise
        if not isinstance(body, dict):
            raise GitHubError("github pull was not an object")
        user = body.get("user") or {}
        base = body.get("base") or {}
        head = body.get("head") or {}
        return GitHubPull(
            number=int(body.get("number") or number),
            title=str(body.get("title") or ""),
            body=str(body.get("body") or ""),
            state=str(body.get("state") or "open"),
            draft=bool(body.get("draft")),
            html_url=str(body.get("html_url") or ""),
            user=str(user.get("login") or ""),
            base=str(base.get("ref") or ""),
            head=str(head.get("ref") or ""),
            additions=int(body.get("additions") or 0),
            deletions=int(body.get("deletions") or 0),
            changed_files=int(body.get("changed_files") or 0),
            commits=int(body.get("commits") or 0),
        )

    async def list_pull_files(
        self, token: str, full_name: str, number: int, per_page: int = 30
    ) -> list[GitHubPullFile]:
        body = await self._get_json(
            f"https://api.github.com/repos/{full_name}/pulls/{number}/files",
            token,
            params={"per_page": per_page},
        )
        if not isinstance(body, list):
            raise GitHubError("github pull files were not a list")
        files: list[GitHubPullFile] = []
        for item in body:
            if not isinstance(item, dict):
                continue
            patch = str(item.get("patch") or "")
            if len(patch) > 12_000:
                patch = patch[:12_000] + "\n…"
            files.append(
                GitHubPullFile(
                    filename=str(item.get("filename") or ""),
                    status=str(item.get("status") or ""),
                    additions=int(item.get("additions") or 0),
                    deletions=int(item.get("deletions") or 0),
                    patch=patch,
                )
            )
        return files

    async def _get_json(self, url: str, token: str, params: dict | None = None) -> dict | list:
        response = await self._request(
            "GET",
            url,
            params=params,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "codeloom-cloud",
            },
        )
        return response.json()

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30)
        response = await self._client.request(method, url, **kwargs)
        if response.status_code >= 400:
            raise GitHubError(
                f"github request failed ({response.status_code})",
                status=response.status_code,
            )
        return response


def _repo(item: dict) -> GitHubRepo:
    owner = item.get("owner") or {}
    full_name = str(item.get("full_name") or "")
    name = str(item.get("name") or "")
    owner_login = str(owner.get("login") or "")
    if not owner_login and "/" in full_name:
        owner_login = full_name.split("/", 1)[0]
    return GitHubRepo(
        full_name=full_name,
        owner=owner_login,
        name=name,
        default_branch=str(item.get("default_branch") or "main"),
        private=bool(item.get("private")),
        description=item.get("description"),
        language=item.get("language"),
        stars=int(item.get("stargazers_count") or 0),
        forks=int(item.get("forks_count") or 0),
        open_issues=int(item.get("open_issues_count") or 0),
        html_url=str(item.get("html_url") or ""),
        pushed_at=item.get("pushed_at") if isinstance(item.get("pushed_at"), str) else None,
    )


def _work_item(item: dict) -> GitHubWorkItem:
    user = item.get("user") or {}
    updated = item.get("updated_at")
    return GitHubWorkItem(
        number=int(item.get("number") or 0),
        title=str(item.get("title") or ""),
        html_url=str(item.get("html_url") or ""),
        user=str(user.get("login") or ""),
        updated_at=updated if isinstance(updated, str) else None,
    )
