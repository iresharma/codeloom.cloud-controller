from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from codeloom_cloud.config import Settings


class GitHubError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


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

    async def exchange_code(self, code: str) -> str:
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
        body = response.json()
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            description = body.get("error_description") or "github token exchange failed"
            raise GitHubError(str(description))
        return token

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
    )
