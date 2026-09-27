from __future__ import annotations

import json
import tempfile
from pathlib import Path

from codeloom_cloud.sandbox.driver import SandboxHandle


class FakeEngine:
    """In-process stand-in for the engine's Unix socket."""

    def __init__(self, socket_path: Path, fail_start: bool = False) -> None:
        self.socket_path = socket_path
        self.fail_start = fail_start
        self.received: list[dict] = []
        self._server = None

    async def start(self) -> None:
        import asyncio

        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            self.socket_path.unlink()
        self._server = await asyncio.start_unix_server(self._handle, path=str(self.socket_path))

    async def _handle(self, reader, writer) -> None:
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                command = json.loads(line)
                self.received.append(command)
                for event in self._reply(command):
                    writer.write((json.dumps(event) + "\n").encode())
                await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    def _reply(self, command: dict) -> list[dict]:
        command_type = command.get("type")
        if command_type == "StartSession":
            if self.fail_start:
                return [{"type": "ErrorOccurred", "message": "workspace mismatch"}]
            return [self.snapshot()]
        if command_type == "RequestSnapshot":
            return [self.snapshot()]
        if command_type == "RequestAgentTranscript":
            agent_id = command.get("agent_id") or ""
            return [
                {
                    "type": "ChatHistoryAdded",
                    "id": "h1",
                    "role": "assistant",
                    "text": "working",
                    "ts": "2026-01-01T00:00:00Z",
                    "index": 0,
                    "total": 1,
                    "agent_id": agent_id,
                },
                {"type": "ChatHistoryComplete", "count": 1, "agent_id": agent_id},
            ]
        if command_type == "SubmitUserMessage":
            return [
                {
                    "type": "ChatMessageAdded",
                    "id": "m1",
                    "role": "user",
                    "text": command.get("text") or "",
                    "ts": "2026-01-01T00:00:00Z",
                }
            ]
        if command_type == "Shutdown":
            return [{"type": "SessionEnded", "reason": "shutdown"}]
        return []

    def snapshot(self) -> dict:
        return {
            "type": "SnapshotReady",
            "snapshot": {
                "session_id": "engine-session",
                "workspace": "/workspace",
                "agents": [],
                "message_count": 0,
            },
        }

    async def close(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None


class FakeSandboxDriver:
    def __init__(self, fail_start: bool = False, fail_engine: bool = False) -> None:
        self.fail_start = fail_start
        self.fail_engine = fail_engine
        self.engines: dict[str, FakeEngine] = {}
        self.envs: dict[str, dict[str, str]] = {}
        self.running: dict[str, bool] = {}
        self.stopped: list[str] = []
        self.log_text = ""

    async def start(
        self,
        session_id: str,
        host_workspace: Path,
        env: dict[str, str],
    ) -> SandboxHandle:
        if self.fail_start:
            raise RuntimeError("sandbox failed to start")
        host_workspace.mkdir(parents=True, exist_ok=True)
        socket_path = Path(tempfile.gettempdir()) / f"cle-{session_id[:12]}.sock"
        engine = FakeEngine(socket_path, fail_start=self.fail_engine)
        await engine.start()
        container_id = f"fake-{session_id}"
        self.engines[session_id] = engine
        self.envs[session_id] = env
        self.running[container_id] = True
        return SandboxHandle(container_id=container_id, socket_path=socket_path)

    async def logs(self, container_id: str, tail: int = 80) -> str:
        return getattr(self, "log_text", "")

    async def stop(self, container_id: str) -> None:
        if container_id not in self.stopped:
            self.stopped.append(container_id)
        self.running[container_id] = False
        session_id = container_id.removeprefix("fake-")
        engine = self.engines.get(session_id)
        if engine is not None:
            await engine.close()

    async def is_running(self, container_id: str) -> bool:
        return self.running.get(container_id, False)
