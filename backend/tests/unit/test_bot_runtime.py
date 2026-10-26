import asyncio
import json
import logging
from datetime import UTC, datetime

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, Update, User

from tests.fakes.telegram_session import FakeTelegramSession


async def test_polling_drain_keeps_handler_alive_until_its_reply() -> None:
    from taskbot.entrypoints.bot.runtime import InFlightUpdates

    session = FakeTelegramSession()
    entered, finish = asyncio.Event(), asyncio.Event()
    flight = InFlightUpdates()
    dispatcher = Dispatcher(disable_fsm=True)
    dispatcher.update.outer_middleware(flight)

    async def handle(message: Message, bot: Bot) -> None:
        entered.set()
        await finish.wait()
        await bot.send_message(7, "Finished")

    dispatcher.message.register(handle)
    async with Bot("123456:synthetic", session=session) as bot:
        await bot.me()
        update = Update(
            update_id=1,
            message=Message(
                message_id=1,
                date=datetime.now(UTC),
                chat=Chat(id=7, type="private"),
                from_user=User(id=7, is_bot=False, first_name="Synthetic"),
                text="Buy milk",
            ),
        )
        session.responses.append(
            (
                200,
                json.dumps(
                    {"ok": True, "result": [update.model_dump(mode="json", exclude_none=True)]}
                ),
            )
        )
        polling = asyncio.create_task(
            dispatcher.start_polling(bot, handle_signals=False, close_bot_session=False)
        )
        async with asyncio.timeout(2):
            await entered.wait()
            await dispatcher.stop_polling()
            await polling
            draining = asyncio.create_task(flight.drain(1))
            await asyncio.sleep(0)
            assert not draining.done()
            finish.set()
            await draining
        assert "sendMessage" in session.methods
        assert not session.closed
    assert session.closed


def test_token_filter_redacts_formatted_errors_and_tracebacks() -> None:
    from taskbot.entrypoints.bot.runtime import TokenRedactionFilter

    record = logging.LogRecord(
        "synthetic", logging.ERROR, "test", 1, "failed %s", ("bot123456:synthetic/file",), None
    )
    try:
        raise RuntimeError("https://api.telegram.org/file/bot123456:synthetic/voice.ogg")
    except RuntimeError:
        import sys

        record.exc_info = sys.exc_info()
    assert TokenRedactionFilter("123456:synthetic").filter(record)
    formatted = logging.Formatter().format(record)
    assert "123456:synthetic" not in formatted
    assert "***" in formatted


@pytest.mark.parametrize("skip", [False, True])
async def test_drain_cancels_handler_on_deadline_or_second_signal(skip: bool) -> None:
    from taskbot.entrypoints.bot.runtime import InFlightUpdates

    session = FakeTelegramSession()
    entered, cancelled = asyncio.Event(), asyncio.Event()
    flight = InFlightUpdates()
    dispatcher = Dispatcher(disable_fsm=True)
    dispatcher.update.outer_middleware(flight)

    async def handle(message: Message) -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    dispatcher.message.register(handle)
    async with Bot("123456:synthetic", session=session) as bot:
        await bot.me()
        item = Update(
            update_id=1,
            message=Message(
                message_id=1,
                date=datetime.now(UTC),
                chat=Chat(id=7, type="private"),
                from_user=User(id=7, is_bot=False, first_name="Synthetic"),
                text="Buy milk",
            ),
        )
        session.responses.append(
            (
                200,
                json.dumps(
                    {"ok": True, "result": [item.model_dump(mode="json", exclude_none=True)]}
                ),
            )
        )
        polling = asyncio.create_task(
            dispatcher.start_polling(bot, handle_signals=False, close_bot_session=False)
        )
        async with asyncio.timeout(2):
            await entered.wait()
            await dispatcher.stop_polling()
            await polling
            second = asyncio.Event()
            if skip:
                second.set()
            await flight.drain(10 if skip else 0.02, skip=second)
        assert cancelled.is_set()


async def test_error_hook_reply_is_part_of_the_drain() -> None:
    from aiogram import Router
    from aiogram.types import ErrorEvent

    from taskbot.entrypoints.bot.runtime import InFlightUpdates

    session = FakeTelegramSession()
    entered, finish = asyncio.Event(), asyncio.Event()
    dispatcher = Dispatcher(disable_fsm=True)
    flight = InFlightUpdates()
    dispatcher.update.outer_middleware(flight)
    router = Router()

    async def handle(message: Message) -> None:
        raise RuntimeError("synthetic handler failure")

    async def error(event: ErrorEvent, bot: Bot) -> bool:
        entered.set()
        await finish.wait()
        await bot.send_message(7, "Error reply")
        return True

    router.message.register(handle)
    router.errors.register(error)
    dispatcher.include_router(router)
    async with Bot("123456:synthetic", session=session) as bot:
        await bot.me()
        item = Update(
            update_id=1,
            message=Message(
                message_id=1,
                date=datetime.now(UTC),
                chat=Chat(id=7, type="private"),
                from_user=User(id=7, is_bot=False, first_name="Synthetic"),
                text="Buy milk",
            ),
        )
        session.responses.append(
            (
                200,
                json.dumps(
                    {"ok": True, "result": [item.model_dump(mode="json", exclude_none=True)]}
                ),
            )
        )
        polling = asyncio.create_task(
            dispatcher.start_polling(bot, handle_signals=False, close_bot_session=False)
        )
        async with asyncio.timeout(2):
            await entered.wait()
            await dispatcher.stop_polling()
            await polling
            drain = asyncio.create_task(flight.drain(1))
            await asyncio.sleep(0)
            assert not drain.done()
            finish.set()
            await drain
        assert "sendMessage" in session.methods


async def test_poll_health_uses_successful_getupdates_only() -> None:
    from taskbot.entrypoints.bot.runtime import PollingWatch

    watch = PollingWatch(failure_seconds=0.03)
    watch.start()
    session = FakeTelegramSession()
    session.middleware(watch)
    async with Bot("123456:synthetic", session=session) as bot:
        await bot.me()
        assert not watch.healthy()
        await bot.get_updates()
        assert watch.healthy()
        await asyncio.sleep(0.04)
        assert watch.failed()


def test_token_filter_handles_a_token_bearing_telegram_network_error() -> None:
    from aiogram.exceptions import TelegramNetworkError
    from aiogram.methods import GetUpdates
    from aiohttp import ClientConnectionError

    from taskbot.entrypoints.bot.runtime import TokenRedactionFilter

    upstream = ClientConnectionError("https://api.telegram.org/bot123456:synthetic/getUpdates")
    error = TelegramNetworkError(method=GetUpdates(), message=str(upstream))
    record = logging.LogRecord("synthetic", logging.ERROR, "test", 1, "%s", (error,), None)
    TokenRedactionFilter("123456:synthetic").filter(record)
    assert "123456:synthetic" not in logging.Formatter().format(record)
