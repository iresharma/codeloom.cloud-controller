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
    openrouter_model: str = "openai/gpt-5.6-luna"
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
    # Build any missing sandbox image on startup. Only runs for the real
    # Docker driver — never under tests, which use a fake sandbox driver.
    sandbox_build_on_startup: bool = True
    # Overrides for a checkout that isn't laid out as workspace/cloud-controller
    # next to workspace/engine. Left unset, both are found relative to this
    # file's own location in a `codeloom` checkout.
    sandbox_dir: Path | None = None
    engine_path: Path | None = None

    @field_validator("host_data_dir", "sandbox_dir", "engine_path", mode="before")
    @classmethod
    def blank_optional_path(cls, value: object) -> object:
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

    @property
    def resolved_sandbox_dir(self) -> Path:
        """Where sandbox/Dockerfile lives: this package's own checkout root."""
        if self.sandbox_dir is not None:
            return self.sandbox_dir.expanduser().resolve()
        return (Path(__file__).resolve().parents[2] / "sandbox").resolve()

    @property
    def resolved_engine_path(self) -> Path:
        """The engine checkout the sandbox images build from.

        Defaults to workspace/engine, a sibling of workspace/cloud-controller
        in the umbrella `codeloom` checkout — the same layout the README's
        manual ``docker build --build-context engine=../engine`` assumes.
        """
        if self.engine_path is not None:
            return self.engine_path.expanduser().resolve()
        return (Path(__file__).resolve().parents[3] / "engine").resolve()
