from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

ENGINE_PROXY_PORT = 7422


@dataclass(frozen=True)
class SandboxHandle:
    container_id: str
    socket_path: Path
    engine_host: str | None = None
    engine_port: int | None = None


def encode_engine_endpoint(handle: SandboxHandle) -> str:
    if handle.engine_host and handle.engine_port:
        return f"tcp://{handle.engine_host}:{handle.engine_port}"
    return str(handle.socket_path)


def parse_engine_endpoint(value: str) -> tuple[str | None, int | None, Path | None]:
    if value.startswith("tcp://"):
        rest = value.removeprefix("tcp://")
        host, separator, port = rest.rpartition(":")
        if not separator:
            raise ValueError(f"invalid engine endpoint: {value}")
        return host, int(port), None
    return None, None, Path(value)


class SandboxDriver(Protocol):
    async def start(
        self,
        session_id: str,
        host_workspace: Path,
        env: dict[str, str],
    ) -> SandboxHandle: ...

    async def stop(self, container_id: str) -> None: ...

    async def is_running(self, container_id: str) -> bool: ...

    async def logs(self, container_id: str, tail: int = 80) -> str: ...
