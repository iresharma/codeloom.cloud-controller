from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codeloom_cloud.app import create_app
from codeloom_cloud.config import Settings
from codeloom_cloud.sandbox.fake import FakeSandboxDriver
from tests.github_fake import FakeGitHub


@pytest.fixture
def settings():
    root = Path(tempfile.mkdtemp(prefix="cl"))
    data = root / "data"
    data.mkdir()
    cfg = Settings(
        database_url=f"sqlite:///{root / 't.db'}",
        data_dir=data,
        session_secret="test-secret",
        token_encryption_key="",
        github_client_id="cid",
        github_client_secret="csecret",
        github_oauth_callback_url="http://localhost:8000/auth/github/callback",
        frontend_origin="http://localhost:3000",
        openrouter_api_key="sk-test",
        sandbox_image="codeloom-sandbox:test",
        engine_ref="main",
        engine_ready_timeout=5,
        socket_wait_timeout=5,
    )
    yield cfg
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def github() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
def driver() -> FakeSandboxDriver:
    return FakeSandboxDriver()


@pytest.fixture
def app(settings, driver, github):
    return create_app(settings, driver=driver, github=github)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client

