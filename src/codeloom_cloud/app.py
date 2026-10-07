from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from codeloom_cloud.api.auth import router as auth_router
from codeloom_cloud.api.github import router as github_router
from codeloom_cloud.api.projects import router as projects_router
from codeloom_cloud.api.sessions import router as sessions_router
from codeloom_cloud.auth.github import GitHubAPI
from codeloom_cloud.config import Settings
from codeloom_cloud.db import init_db
from codeloom_cloud.memory_store import FileMemoryStore, MemoryStore
from codeloom_cloud.sandbox.build import ensure_sandbox_images
from codeloom_cloud.sandbox.docker import DockerSandboxDriver
from codeloom_cloud.sandbox.driver import SandboxDriver
from codeloom_cloud.sessions.manager import SessionManager

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    driver: SandboxDriver | None = None,
    github: GitHubAPI | None = None,
    memory_store: MemoryStore | None = None,
) -> FastAPI:
    settings = settings or Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    init_db(settings.database_url)
    if driver is None:
        driver = DockerSandboxDriver(settings)
    github = github or GitHubAPI(settings)
    memory_store = memory_store or FileMemoryStore(settings.data_dir / "memory")
    manager = SessionManager(settings, driver, github, memory_store=memory_store)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(level=logging.INFO)
        if isinstance(driver, DockerSandboxDriver):
            # Never fatal: a build failure just leaves sessions to fail with
            # their own clear "image not found" error later.
            try:
                await ensure_sandbox_images(settings)
            except Exception:
                logger.exception("sandbox image build step failed")
        await app.state.manager.reattach()
        yield
        await app.state.manager.aclose()
        await app.state.github.aclose()

    app = FastAPI(title="CodeLoom cloud controller", lifespan=lifespan)
    app.state.settings = settings
    app.state.manager = manager
    app.state.github = github
    app.state.memory_store = memory_store
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth_router)
    app.include_router(github_router)
    app.include_router(projects_router)
    app.include_router(sessions_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
