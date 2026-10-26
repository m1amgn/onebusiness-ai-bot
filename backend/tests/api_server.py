"""An owned in-process HTTP server for streaming integration tests."""

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI


async def _stop_server(server: uvicorn.Server, task: asyncio.Task[None]) -> None:
    server.should_exit = True
    try:
        async with asyncio.timeout(3):
            await asyncio.shield(task)
    except TimeoutError:
        server.force_exit = True
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@asynccontextmanager
async def api_server(app: FastAPI) -> AsyncIterator[str]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    task: asyncio.Task[None] | None = None
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            lifespan="off",
            access_log=False,
            log_level="error",
            timeout_graceful_shutdown=1,
        )
    )
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        task = asyncio.create_task(server.serve(sockets=[listener]))
        async with asyncio.timeout(3):
            while not server.started:
                if task.done():
                    task.result()
                    raise RuntimeError("API server stopped before startup")
                await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{listener.getsockname()[1]}"
    finally:
        try:
            if task is not None:
                await _stop_server(server, task)
        finally:
            listener.close()


async def read_frame(
    lines: AsyncIterator[str], timeout_seconds: float = 2
) -> tuple[str, ...] | None:
    frame: list[str] = []
    async with asyncio.timeout(timeout_seconds):
        async for line in lines:
            if line:
                frame.append(line)
            elif frame:
                return tuple(frame)
    assert not frame, "Stream ended inside an SSE frame"
    return None


def assert_stream_headers(response: httpx.Response) -> None:
    assert response.status_code == 200
    assert response.headers["content-type"].split(";")[0] == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"


async def assert_ticket_rejected(client: httpx.AsyncClient, ticket: str | None = None) -> None:
    params = {} if ticket is None else {"ticket": ticket}
    async with client.stream("GET", "/api/events", params=params) as response:
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        await response.aread()
        assert response.json() == {"detail": "Send /dashboard to the bot for a new link"}
