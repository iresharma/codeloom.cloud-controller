from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from codeloom_cloud.config import Settings
from codeloom_cloud.sandbox.images import RUNTIMES, image_for

logger = logging.getLogger(__name__)


def _image_exists(client: object, tag: str) -> bool:
    import docker

    try:
        client.images.get(tag)  # type: ignore[attr-defined]
        return True
    except docker.errors.ImageNotFound:
        return False


async def _build_image(tag: str, runtime: str, sandbox_dir: Path, engine_dir: Path) -> None:
    cmd = [
        "docker",
        "build",
        "--target",
        runtime,
        "-t",
        tag,
        "-f",
        str(sandbox_dir / "Dockerfile"),
        "--build-context",
        f"engine={engine_dir}",
        str(sandbox_dir),
    ]
    logger.info("building sandbox image %s (first run only; this can take a few minutes)", tag)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None
    async for raw in proc.stdout:
        line = raw.decode("utf-8", errors="replace").rstrip()
        if line:
            logger.info("[docker build %s] %s", tag, line)
    code = await proc.wait()
    if code != 0:
        raise RuntimeError(f"docker build failed for {tag} (exit code {code})")
    logger.info("built sandbox image %s", tag)


async def ensure_sandbox_images(settings: Settings) -> None:
    """Build whichever sandbox images are missing, once, on startup.

    Never rebuilds an image that already exists — this only fills gaps, so a
    repeat startup with every image present is an instant no-op. It never
    raises: a missing source tree, an unreachable Docker daemon, or a failed
    build is logged and the controller still starts. Sessions that need the
    image then fail with their own clear error instead.
    """
    if not settings.sandbox_build_on_startup:
        return

    sandbox_dir = settings.resolved_sandbox_dir
    engine_dir = settings.resolved_engine_path
    if not (sandbox_dir / "Dockerfile").is_file():
        logger.warning("sandbox image build skipped: no Dockerfile at %s", sandbox_dir)
        return
    if not engine_dir.is_dir():
        logger.warning("sandbox image build skipped: no engine checkout at %s", engine_dir)
        return

    try:
        import docker

        client = docker.from_env()
    except Exception as exc:
        logger.warning("sandbox image build skipped: docker is not reachable (%s)", exc)
        return

    try:
        missing = [rt for rt in RUNTIMES if not _image_exists(client, image_for(settings.sandbox_image, rt))]
    except Exception as exc:
        logger.warning("sandbox image build skipped: could not list images (%s)", exc)
        return
    if not missing:
        return

    logger.info("building %d missing sandbox image(s): %s", len(missing), ", ".join(missing))
    for runtime in missing:
        tag = image_for(settings.sandbox_image, runtime)
        try:
            await _build_image(tag, runtime, sandbox_dir, engine_dir)
        except Exception:
            logger.exception("failed to build sandbox image %s", tag)
