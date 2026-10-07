from __future__ import annotations

from pathlib import Path

import docker

from codeloom_cloud.config import Settings
from codeloom_cloud.sandbox.build import ensure_sandbox_images


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(
        session_secret="test-secret-test-secret-test-secret",
        sandbox_dir=overrides.pop("sandbox_dir", tmp_path / "sandbox"),
        engine_path=overrides.pop("engine_path", tmp_path / "engine"),
        **overrides,
    )


def test_resolved_paths_default_to_the_sibling_checkout():
    settings = Settings(session_secret="test-secret-test-secret-test-secret")
    assert settings.resolved_sandbox_dir.name == "sandbox"
    assert settings.resolved_sandbox_dir.parent.name == "cloud-controller"
    assert settings.resolved_engine_path.name == "engine"
    assert settings.resolved_engine_path.parent == settings.resolved_sandbox_dir.parent.parent


def test_resolved_paths_honor_explicit_overrides(tmp_path):
    settings = _settings(tmp_path)
    assert settings.resolved_sandbox_dir == (tmp_path / "sandbox").resolve()
    assert settings.resolved_engine_path == (tmp_path / "engine").resolve()


async def test_skipped_when_disabled(tmp_path):
    settings = _settings(tmp_path, sandbox_build_on_startup=False)
    # tmp_path/sandbox and tmp_path/engine don't even exist — this would
    # warn-and-return either way, but confirms the flag short-circuits first.
    await ensure_sandbox_images(settings)


async def test_skipped_without_a_dockerfile(tmp_path):
    settings = _settings(tmp_path)
    (tmp_path / "engine").mkdir()
    # tmp_path/sandbox does not exist at all, let alone a Dockerfile.
    await ensure_sandbox_images(settings)


async def test_skipped_without_an_engine_checkout(tmp_path):
    settings = _settings(tmp_path)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "Dockerfile").write_text("FROM scratch\n")
    # tmp_path/engine does not exist.
    await ensure_sandbox_images(settings)


async def test_skipped_when_docker_is_unreachable(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "Dockerfile").write_text("FROM scratch\n")
    (tmp_path / "engine").mkdir()


    def _boom():
        raise RuntimeError("no daemon")

    monkeypatch.setattr(docker, "from_env", _boom)
    await ensure_sandbox_images(settings)  # must not raise


async def test_builds_only_missing_images(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "Dockerfile").write_text("FROM scratch\n")
    (tmp_path / "engine").mkdir()


    class FakeImages:
        def __init__(self, present: set[str]) -> None:
            self.present = present

        def get(self, tag: str) -> object:
            if tag not in self.present:
                raise docker.errors.ImageNotFound(tag)
            return object()

    class FakeClient:
        def __init__(self, present: set[str]) -> None:
            self.images = FakeImages(present)

    fake_client = FakeClient({"codeloom-sandbox:python", "codeloom-sandbox:node"})
    monkeypatch.setattr(docker, "from_env", lambda: fake_client)

    built: list[str] = []

    async def fake_build(tag, runtime, sandbox_dir, engine_dir):
        built.append(tag)

    monkeypatch.setattr("codeloom_cloud.sandbox.build._build_image", fake_build)

    await ensure_sandbox_images(settings)

    assert built == ["codeloom-sandbox:golang"]


async def test_noop_when_every_image_already_exists(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "Dockerfile").write_text("FROM scratch\n")
    (tmp_path / "engine").mkdir()


    class FakeImages:
        def get(self, tag: str) -> object:
            return object()

    class FakeClient:
        images = FakeImages()

    monkeypatch.setattr(docker, "from_env", lambda: FakeClient())

    async def fail_build(*args, **kwargs):
        raise AssertionError("should not build an image that already exists")

    monkeypatch.setattr("codeloom_cloud.sandbox.build._build_image", fail_build)

    await ensure_sandbox_images(settings)


async def test_one_failed_build_does_not_stop_the_others(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "Dockerfile").write_text("FROM scratch\n")
    (tmp_path / "engine").mkdir()


    class FakeImages:
        def get(self, tag: str) -> object:
            raise docker.errors.ImageNotFound(tag)

    class FakeClient:
        images = FakeImages()

    monkeypatch.setattr(docker, "from_env", lambda: FakeClient())

    attempted: list[str] = []

    async def flaky_build(tag, runtime, sandbox_dir, engine_dir):
        attempted.append(tag)
        if runtime == "python":
            raise RuntimeError("docker build failed for codeloom-sandbox:python")

    monkeypatch.setattr("codeloom_cloud.sandbox.build._build_image", flaky_build)

    await ensure_sandbox_images(settings)  # must not raise

    assert set(attempted) == {
        "codeloom-sandbox:python",
        "codeloom-sandbox:node",
        "codeloom-sandbox:golang",
    }
