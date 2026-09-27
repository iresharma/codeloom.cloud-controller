from __future__ import annotations

from codeloom_cloud.auth.github import GitHubError, GitHubProfile, GitHubRepo


class FakeGitHub:
    def __init__(self) -> None:
        self.users: dict[str, GitHubProfile] = {}
        self.repos: dict[str, list[GitHubRepo]] = {}

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

    async def exchange_code(self, code: str) -> str:
        if code not in self.users:
            raise GitHubError("bad code")
        return code

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

    async def aclose(self) -> None:
        return None
