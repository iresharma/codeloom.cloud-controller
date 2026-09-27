"""Forward a TCP port to the engine Unix socket.

Docker Desktop cannot connect to a Linux Unix socket through a bind mount,
so the controller reaches the engine through this hop. The host publish is
bound to 127.0.0.1; the proxy listens on all interfaces inside the container
so Docker can forward the published port.

The proxy starts before the engine. Each accepted connection waits for the
Unix socket so clone/boot does not look like a dead port.
"""

from __future__ import annotations

import asyncio
import os
import sys

UNIX = os.environ.get("ENGINE_SOCKET", "/workspace/.engine/engine.sock")
HOST = os.environ.get("ENGINE_PROXY_HOST", "0.0.0.0")
PORT = int(os.environ.get("ENGINE_PROXY_PORT", "7422"))
WAIT = float(os.environ.get("ENGINE_SOCKET_WAIT", "180"))


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, BrokenPipeError, asyncio.CancelledError):
        return
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def _open_unix() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    deadline = asyncio.get_running_loop().time() + WAIT
    last: Exception | None = None
    while asyncio.get_running_loop().time() < deadline:
        try:
            return await asyncio.open_unix_connection(UNIX)
        except OSError as exc:
            last = exc
            await asyncio.sleep(0.2)
    raise OSError(f"engine socket not ready: {last}")


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        unix_reader, unix_writer = await _open_unix()
    except OSError as exc:
        print(f"tcp_proxy: {exc}", file=sys.stderr, flush=True)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            return
        return
    await asyncio.gather(
        _pipe(reader, unix_writer),
        _pipe(unix_reader, writer),
        return_exceptions=True,
    )


async def main() -> None:
    server = await asyncio.start_server(_handle, HOST, PORT)
    print(f"tcp_proxy: listening on {HOST}:{PORT}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
