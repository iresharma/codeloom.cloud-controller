from __future__ import annotations

from codeloom_cloud.auth.github import (
    GitHubContributor,
    GitHubError,
    GitHubProfile,
    GitHubPull,
    GitHubPullFile,
    GitHubRepo,
    GitHubToken,
    GitHubWorkItem,
)


class FakeGitHub:
    def __init__(self) -> None:
        self.users: dict[str, GitHubProfile] = {}
        self.repos: dict[str, list[GitHubRepo]] = {}
        self.grants: dict[str, GitHubToken] = {}
        self.refresh_grants: dict[str, GitHubToken] = {}
        self.issues: dict[tuple[str, str], list[GitHubWorkItem]] = {}
        self.pulls: dict[tuple[str, str], list[GitHubWorkItem]] = {}
        self.contributors: dict[tuple[str, str], list[GitHubContributor]] = {}
        self.pull_details: dict[tuple[str, str, int], GitHubPull] = {}
        self.pull_files: dict[tuple[str, str, int], list[GitHubPullFile]] = {}

    def add_user(
        self,
        token: str,
        *,
        github_id: int,
        login: str,
        name: str | None = None,
        avatar_url: str | None = None,
    ) -> None:
        self.users[token] = GitHubProfile(
            id=github_id,
            login=login,
            name=name,
            avatar_url=avatar_url,
        )

    def allow(self, token: str, repo: GitHubRepo) -> None:
        self.repos.setdefault(token, []).append(repo)

    def authorize_url(self, state: str) -> str:
        return f"https://github.com/login/oauth/authorize?state={state}"

    async def exchange_code(self, code: str) -> GitHubToken:
        if code not in self.users and code not in self.grants:
            raise GitHubError("bad code")
        return self.grants.get(code, GitHubToken(access_token=code))

    async def refresh_access_token(self, refresh_token: str) -> GitHubToken:
        try:
            return self.refresh_grants[refresh_token]
        except KeyError as exc:
            raise GitHubError("refresh failed", status=401) from exc

    async def get_user(self, token: str) -> GitHubProfile:
        try:
            return self.users[token]
        except KeyError as exc:
            raise GitHubError("unknown user", status=401) from exc

    async def list_repos(self, token: str, page: int, per_page: int) -> list[GitHubRepo]:
        rows = self.repos.get(token, [])
        start = (page - 1) * per_page
        return rows[start : start + per_page]

    async def get_repo(self, token: str, full_name: str) -> GitHubRepo | None:
        for repo in self.repos.get(token, []):
            if repo.full_name == full_name:
                return repo
        return None

    async def list_issues(self, token: str, full_name: str, per_page: int = 8) -> list[GitHubWorkItem]:
        return self.issues.get((token, full_name), [])[:per_page]

    async def list_pulls(self, token: str, full_name: str, per_page: int = 8) -> list[GitHubWorkItem]:
        return self.pulls.get((token, full_name), [])[:per_page]

    async def list_contributors(
        self, token: str, full_name: str, per_page: int = 8
    ) -> list[GitHubContributor]:
        return self.contributors.get((token, full_name), [])[:per_page]

    async def get_pull(self, token: str, full_name: str, number: int) -> GitHubPull | None:
        return self.pull_details.get((token, full_name, number))

    async def list_pull_files(
        self, token: str, full_name: str, number: int, per_page: int = 30
    ) -> list[GitHubPullFile]:
        return self.pull_files.get((token, full_name, number), [])[:per_page]

    async def aclose(self) -> None:
        return None
