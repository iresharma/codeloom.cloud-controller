from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from codeloom_cloud.config import Settings
from codeloom_cloud.sandbox.driver import ENGINE_PROXY_PORT, SandboxHandle

logger = logging.getLogger(__name__)


class DockerSandboxDriver:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = None

    def _docker(self):
        if self._client is None:
            import docker

            self._client = docker.from_env()
        return self._client

    async def start(
        self,
        session_id: str,
        host_workspace: Path,
        env: dict[str, str],
        image: str,
    ) -> SandboxHandle:
        source = self.settings.docker_bind_source(host_workspace)
        memory = self.settings.sandbox_memory
        cpu_period = 100_000
        cpu_quota = int(self.settings.sandbox_cpus * cpu_period)

        def _run():
            return self._docker().containers.run(
                image,
                detach=True,
                name=f"codeloom-session-{session_id}",
                environment=env,
                volumes={source: {"bind": "/workspace", "mode": "rw"}},
                # Unix sockets on a Docker Desktop bind mount do not work.
                # Keep the repo on the mount; put .engine on a Linux tmpfs.
                # Docker's tmpfs default includes noexec. Writer worktrees and
                # their node_modules live here, so binaries must be executable.
                tmpfs={"/workspace/.engine": "rw,exec,mode=1777"},
                ports={f"{ENGINE_PROXY_PORT}/tcp": ("127.0.0.1", None)},
                mem_limit=memory,
                cpu_period=cpu_period,
                cpu_quota=cpu_quota,
                network_mode="bridge",
            )

        try:
            container = await asyncio.to_thread(_run)
            host, port = await asyncio.to_thread(self._published_port, container)
        except Exception as exc:
            logger.exception("sandbox start failed for %s", session_id)
            raise RuntimeError(f"failed to start sandbox: {exc}") from exc
        return SandboxHandle(
            container_id=container.id,
            socket_path=host_workspace / ".engine" / "engine.sock",
            engine_host=host,
            engine_port=port,
        )

    def _published_port(self, container) -> tuple[str, int]:
        container.reload()
        bindings = (container.ports or {}).get(f"{ENGINE_PROXY_PORT}/tcp") or []
        if not bindings:
            raise RuntimeError(f"sandbox did not publish {ENGINE_PROXY_PORT}/tcp")
        host = bindings[0].get("HostIp") or "127.0.0.1"
        port = bindings[0].get("HostPort")
        if not port:
            raise RuntimeError(f"sandbox did not publish {ENGINE_PROXY_PORT}/tcp")
        return host, int(port)

    async def stop(self, container_id: str) -> None:
        def _stop() -> None:
            import docker

            try:
                container = self._docker().containers.get(container_id)
            except docker.errors.NotFound:
                return
            try:
                container.stop(timeout=10)
            except docker.errors.NotFound:
                return
            try:
                container.remove(force=True)
            except docker.errors.NotFound:
                return

        await asyncio.to_thread(_stop)

    async def is_running(self, container_id: str) -> bool:
        def _running() -> bool:
            import docker

            try:
                container = self._docker().containers.get(container_id)
            except docker.errors.NotFound:
                return False
            container.reload()
            return container.status == "running"

        return await asyncio.to_thread(_running)

    async def logs(self, container_id: str, tail: int = 80) -> str:
        def _logs() -> str:
            import docker

            try:
                container = self._docker().containers.get(container_id)
            except docker.errors.NotFound:
                return ""
            raw = container.logs(tail=tail)
            if isinstance(raw, bytes):
                return raw.decode("utf-8", errors="replace")
            return str(raw)

        return await asyncio.to_thread(_logs)
