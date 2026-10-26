import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware, NextRequestMiddlewareType
from aiogram.methods import GetUpdates, Response, TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram.types import TelegramObject

# aiogram's update middleware contract carries heterogeneous handler data and results.
type UpdateHandler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class InFlightUpdates(BaseMiddleware):
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[object]] = set()

    async def __call__(
        self, handler: UpdateHandler, event: TelegramObject, data: dict[str, Any]
    ) -> Any:  # aiogram defines dynamic handler data and result types.
        task = asyncio.current_task()
        if task is not None and task not in self._tasks:
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return await handler(event, data)

    async def drain(self, timeout: float, *, skip: asyncio.Event | None = None) -> None:  # noqa: ASYNC109 - Caller-owned shutdown budget.
        tasks = {
            task for task in self._tasks if task is not asyncio.current_task() and not task.done()
        }
        if not tasks:
            return
        waiter = asyncio.create_task(asyncio.wait(tasks, timeout=timeout))
        skip_waiter = None if skip is None else asyncio.create_task(skip.wait())
        try:
            if skip_waiter is None:
                await waiter
            else:
                await asyncio.wait({waiter, skip_waiter}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            waiter.cancel()
            if skip_waiter is not None:
                skip_waiter.cancel()
                await asyncio.gather(skip_waiter, return_exceptions=True)
            await asyncio.gather(waiter, return_exceptions=True)


class PollingWatch(BaseRequestMiddleware):
    def __init__(self, *, failure_seconds: float = 60) -> None:
        self._failure_seconds = failure_seconds
        self._started: float | None = None
        self._last_success: float | None = None
        self.last_error = "NoSuccessfulPoll"

    def start(self) -> None:
        self._started = asyncio.get_running_loop().time()

    def healthy(self) -> bool:
        return self._last_success is not None and not self.failed()

    def failed(self) -> bool:
        since = self._last_success if self._last_success is not None else self._started
        return (
            since is not None and asyncio.get_running_loop().time() - since >= self._failure_seconds
        )

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        try:
            result = await make_request(bot, method)
        except Exception as error:
            if isinstance(method, GetUpdates):
                self.last_error = type(error).__name__
            raise
        if isinstance(method, GetUpdates):
            self._last_success = asyncio.get_running_loop().time()
        return result


class TokenRedactionFilter(logging.Filter):
    def __init__(self, token: str) -> None:
        super().__init__()
        self._token = token

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = record.getMessage().replace(self._token, "***")
        record.args = ()
        if record.exc_info is not None:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
            record.exc_info = None
        if record.exc_text is not None:
            record.exc_text = record.exc_text.replace(self._token, "***")
        if record.stack_info is not None:
            record.stack_info = record.stack_info.replace(self._token, "***")
        return True
