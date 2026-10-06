from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from codeloom_cloud.engine.client import EngineClient

logger = logging.getLogger(__name__)


class SessionBridge:
    """One engine connection, fanned out to every websocket on the session."""

    def __init__(
        self,
        client: EngineClient,
        on_disconnect: Callable[[], None] | None = None,
        on_memory: Callable[[dict], None] | None = None,
    ) -> None:
        self.client = client
        self.subscribers: list[asyncio.Queue[dict]] = []
        self.ready = asyncio.Event()
        self.start_error: str | None = None
        self.engine_session_id: str | None = None
        self._saw_snapshot = False
        self._task: asyncio.Task[None] | None = None
        self._closing = False
        self._on_disconnect = on_disconnect
        self._on_memory = on_memory

    def start(self) -> None:
        self._task = asyncio.create_task(self._read_loop())

    def subscribe(self) -> asyncio.Queue[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue()
        self.subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict]) -> None:
        if queue in self.subscribers:
            self.subscribers.remove(queue)

    async def send(self, command: dict) -> None:
        await self.client.send(command)

    async def wait_ready(self, timeout: float) -> str:
        await asyncio.wait_for(self.ready.wait(), timeout)
        if self.start_error:
            raise RuntimeError(self.start_error)
        if not self.engine_session_id:
            raise RuntimeError("engine snapshot missing session_id")
        return self.engine_session_id

    def _note(self, event: dict) -> None:
        event_type = event.get("type")
        if event_type == "SnapshotReady" and not self._saw_snapshot:
            snapshot = event.get("snapshot") or {}
            if isinstance(snapshot, dict):
                session_id = snapshot.get("session_id")
                if isinstance(session_id, str):
                    self.engine_session_id = session_id
            self._saw_snapshot = True
            self.ready.set()
        elif event_type == "ErrorOccurred" and not self._saw_snapshot:
            message = event.get("message")
            self.start_error = message if isinstance(message, str) else "engine error"
            self.ready.set()

    def _fanout(self, event: dict) -> None:
        for queue in list(self.subscribers):
            queue.put_nowait(event)

    async def _read_loop(self) -> None:
        try:
            async for event in self.client.events():
                if event.get("type") == "MemoryExported":
                    # Controller-internal: persist it, never fan out to the UI.
                    if self._on_memory is not None:
                        try:
                            self._on_memory(event.get("memory") or {})
                        except Exception:
                            logger.exception("memory persist failed")
                    continue
                self._note(event)
                self._fanout(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            if not self._closing:
                logger.exception("engine read failed")
        finally:
            if self._closing:
                return
            if not self.ready.is_set():
                self.start_error = self.start_error or "engine connection closed"
                self.ready.set()
                return
            self._fanout({"type": "ErrorOccurred", "message": "engine connection closed"})
            if self._on_disconnect is not None:
                self._on_disconnect()

    async def close(self) -> None:
        self._closing = True
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self.client.close()
