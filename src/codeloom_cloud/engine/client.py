from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from codeloom_cloud.engine.commands import STREAM_LIMIT, ProtocolError, encode_command


class EngineClient:
    """NDJSON client for one codeloom.engine connection (Unix socket or TCP)."""

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None

    async def connect(self, socket_path: str) -> None:
        self.reader, self.writer = await asyncio.open_unix_connection(
            socket_path,
            limit=STREAM_LIMIT,
        )

    async def connect_tcp(self, host: str, port: int) -> None:
        self.reader, self.writer = await asyncio.open_connection(
            host,
            port,
            limit=STREAM_LIMIT,
        )

    async def send(self, command: dict) -> None:
        if self.writer is None:
            raise ProtocolError("engine is not connected")
        self.writer.write(encode_command(command, self.workspace))
        await self.writer.drain()

    async def events(self) -> AsyncIterator[dict]:
        if self.reader is None:
            raise ProtocolError("engine is not connected")
        while True:
            line = await self.reader.readline()
            if not line:
                return
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                yield {"type": "ErrorOccurred", "message": f"malformed engine event: {exc}"}
                continue
            if isinstance(event, dict):
                yield event

    async def close(self) -> None:
        writer = self.writer
        self.writer = None
        self.reader = None
        if writer is None:
            return
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            return
