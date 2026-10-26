"""Local TCP proxy with a controllable silent-peer failure mode."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from urllib.parse import urlsplit, urlunsplit


class TcpProxy:
    def __init__(self, target_url: str) -> None:
        self._target = urlsplit(target_url)
        self._traffic = asyncio.Event()
        self._traffic.set()
        self._tasks: set[asyncio.Task[None]] = set()
        self.url = ""

    def freeze(self) -> None:
        self._traffic.clear()

    def unfreeze(self) -> None:
        self._traffic.set()

    async def _pipe(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while True:
            await self._traffic.wait()
            data = await reader.read(65536)
            if not data:
                return
            await self._traffic.wait()
            writer.write(data)
            await writer.drain()

    async def _connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        remote_writer: asyncio.StreamWriter | None = None
        try:
            remote_reader, remote_writer = await asyncio.open_connection(
                self._target.hostname or "localhost", self._target.port or 6379
            )
            tasks = [
                asyncio.create_task(self._pipe(reader, remote_writer)),
                asyncio.create_task(self._pipe(remote_reader, writer)),
            ]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        except ConnectionError, OSError:
            pass
        finally:
            for stream in (writer, remote_writer):
                if stream is not None:
                    stream.close()
                    with suppress(ConnectionError, OSError):
                        await stream.wait_closed()

    def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.create_task(self._connection(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def close(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@asynccontextmanager
async def tcp_proxy(target_url: str) -> AsyncIterator[TcpProxy]:
    proxy = TcpProxy(target_url)
    server = await asyncio.start_server(proxy._accept, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    target = urlsplit(target_url)
    credentials = target.netloc.rsplit("@", 1)[0] + "@" if "@" in target.netloc else ""
    proxy.url = urlunsplit(target._replace(netloc=f"{credentials}127.0.0.1:{port}"))
    try:
        yield proxy
    finally:
        server.close()
        await proxy.close()
        await server.wait_closed()
