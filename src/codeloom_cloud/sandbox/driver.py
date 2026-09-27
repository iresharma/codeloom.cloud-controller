from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class SandboxHandle:
    container_id: str
    socket_path: Path


class SandboxDriver(Protocol):
    async def start(
        self,
        session_id: str,
        host_workspace: Path,
        env: dict[str, str],
    ) -> SandboxHandle: ...

    async def stop(self, container_id: str) -> None: ...

    async def is_running(self, container_id: str) -> bool: ...
