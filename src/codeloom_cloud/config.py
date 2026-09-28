from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "sqlite:///./data/codeloom.db"
    data_dir: Path = Path("./data")
    host_data_dir: Path | None = None
    github_client_id: str = ""
    github_client_secret: str = ""
    github_oauth_callback_url: str = "http://localhost:8000/auth/github/callback"
    frontend_origin: str = "http://localhost:3000"
    session_secret: str = "dev-secret-change-me"
    token_encryption_key: str = ""
    openrouter_api_key: str = ""
    typesafe_api_key: str = ""
    # Repository of the three sandbox images. The tag is replaced with
    # python, node, or golang when a session starts.
    sandbox_image: str = "codeloom-sandbox:python"
    engine_ref: str = "main"
    sandbox_memory: str = "2g"
    sandbox_cpus: float = 2.0
    engine_workspace: str = "/workspace"
    engine_ready_timeout: float = 120.0
    socket_wait_timeout: float = 120.0

    @field_validator("host_data_dir", mode="before")
    @classmethod
    def blank_host_dir(cls, value: object) -> object:
        if value is None or value == "":
            return None
        return value

    @field_validator("token_encryption_key", mode="before")
    @classmethod
    def blank_token_key(cls, value: object) -> object:
        if value is None:
            return ""
        return value

    def docker_bind_source(self, host_workspace: Path) -> str:
        """Path Docker should mount.

        When the controller itself runs in a container, ``data_dir`` is the
        path inside that container and ``host_data_dir`` is the same directory
        as the Docker daemon sees it.
        """
        data = self.data_dir.expanduser().resolve()
        root = (self.host_data_dir or data).expanduser().resolve()
        resolved = host_workspace.expanduser().resolve()
        try:
            relative = resolved.relative_to(data)
        except ValueError:
            return str(resolved)
        return str(root / relative)
