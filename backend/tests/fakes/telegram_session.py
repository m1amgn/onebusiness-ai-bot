import asyncio
import json
from collections import deque
from collections.abc import AsyncGenerator

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods.base import TelegramMethod, TelegramType


class FakeTelegramSession(BaseSession):
    """Exercise aiogram serialization and response validation without HTTP."""

    def __init__(self, *, responses: deque[tuple[int, str]] | None = None) -> None:
        super().__init__()
        self.responses = responses if responses is not None else deque()
        self.methods: list[str] = []
        self.payloads: list[dict[str, object]] = []
        self.timeouts: list[int | None] = []
        self.chunks = [b"audio"]
        self.stream_error: Exception | None = None
        self.stream_gate: asyncio.Event | None = None
        self.request_gates: dict[str, asyncio.Event] = {}
        self.request_errors: dict[str, Exception] = {}
        self.started: asyncio.Event = asyncio.Event()
        self.downloads = 0
        self.closed = False

    async def close(self) -> None:
        self.closed = True

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[TelegramType],
        timeout: int | None = None,  # noqa: ASYNC109 - SDK override.
    ) -> TelegramType:
        name = method.__api_method__
        self.methods.append(name)
        self.payloads.append(method.model_dump(exclude_none=True, exclude_unset=True))
        self.timeouts.append(timeout)
        self.started.set()
        async with asyncio.timeout(timeout if timeout is not None else self.timeout):
            if name in self.request_gates:
                await self.request_gates[name].wait()
            if name in self.request_errors:
                raise self.request_errors[name]
            status, body = self.responses.popleft() if self.responses else self._default(name)
            response = self.check_response(bot, method, status, body)
            if response.result is None:
                raise AssertionError("Fake Telegram response has no result")
            if name == "getUpdates":
                await asyncio.sleep(0.01)
            return response.result

    def _default(self, name: str) -> tuple[int, str]:
        result: object = True
        if name == "getMe":
            result = {
                "id": 123456,
                "is_bot": True,
                "first_name": "Synthetic",
                "username": "synthetic_bot",
            }
        elif name == "getFile":
            result = {"file_id": "file", "file_unique_id": "unique", "file_path": "voice.ogg"}
        elif name == "getUpdates":
            result = []
        elif name == "sendMessage":
            result = {
                "message_id": len(self.methods),
                "date": 0,
                "chat": {"id": 7, "type": "private"},
            }
        return 200, json.dumps({"ok": True, "result": result})

    async def stream_content(
        self,
        url: str,
        headers: dict[str, object] | None = None,
        timeout: int = 30,  # noqa: ASYNC109 - SDK override.
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes]:
        self.downloads += 1
        if self.stream_gate is not None:
            await self.stream_gate.wait()
        if self.stream_error is not None:
            raise self.stream_error
        for chunk in self.chunks:
            yield chunk
