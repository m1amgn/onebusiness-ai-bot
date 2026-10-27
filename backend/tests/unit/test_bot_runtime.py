import asyncio
import json
import logging
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from aiogram import Bot, Dispatcher, Router
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import GetUpdates
from aiogram.types import ErrorEvent, Message
from aiohttp import ClientConnectionError
from redis.asyncio import Redis
from redis.exceptions import ConnectionError

from taskbot.entrypoints.bot import runtime
from taskbot.entrypoints.bot.runtime import InFlightUpdates, PollingWatch, TokenRedactionFilter
from taskbot.infrastructure.redis import bot_lock
from taskbot.infrastructure.redis.bot_lock import (
    LOCK_RENEW_SECONDS,
    LOCK_STOP_AFTER_SECONDS,
    LOCK_TTL_SECONDS,
    REDIS_CALL_TIMEOUT_SECONDS,
    HeartbeatTask,
    LockKeeper,
    PollingLock,
)
from tests.bot import message_update
from tests.fakes.telegram_session import FakeTelegramSession


async def test_polling_drain_keeps_handler_alive_until_its_reply() -> None:
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
        update = message_update(1, "Buy milk")
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
    record = logging.LogRecord(
        "synthetic", logging.ERROR, "test", 1, "failed %s", ("bot123456:synthetic/file",), None
    )
    try:
        raise RuntimeError("https://api.telegram.org/file/bot123456:synthetic/voice.ogg")
    except RuntimeError:
        record.exc_info = sys.exc_info()
    assert TokenRedactionFilter("123456:synthetic").filter(record)
    formatted = logging.Formatter().format(record)
    assert "123456:synthetic" not in formatted
    assert "***" in formatted


@pytest.mark.parametrize("skip", [False, True])
async def test_drain_cancels_handler_on_deadline_or_second_signal(skip: bool) -> None:
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
        item = message_update(1, "Buy milk")
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
        item = message_update(1, "Buy milk")
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


async def test_poll_health_counts_failed_attempts_and_outstanding_requests() -> None:
    watch = PollingWatch(failure_seconds=0.03)
    watch.start()
    session = FakeTelegramSession()
    session.middleware(watch)
    async with Bot("123456:synthetic", session=session) as bot:
        await bot.me()
        assert not watch.healthy()
        await bot.get_updates()
        await asyncio.sleep(0.04)
        assert watch.healthy()
        assert not watch.failed()
        session.request_errors["getUpdates"] = RuntimeError("synthetic")
        with pytest.raises(RuntimeError):
            await bot.get_updates()
        assert not watch.failed()
        await asyncio.sleep(0.04)
        assert watch.failed()
        assert not watch.healthy()
        del session.request_errors["getUpdates"]
        await bot.get_updates()
        assert watch.healthy()
        session.request_gates["getUpdates"] = asyncio.Event()
        pending = asyncio.create_task(bot.get_updates())
        try:
            await asyncio.sleep(0.04)
            assert watch.failed()
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


async def test_poll_failure_streak_keeps_the_first_attempt_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [100.0]
    monkeypatch.setattr(
        runtime,
        "asyncio",
        SimpleNamespace(get_running_loop=lambda: SimpleNamespace(time=lambda: now[0])),
    )
    watch = PollingWatch(failure_seconds=6)
    watch.start()
    session = FakeTelegramSession()
    session.middleware(watch)
    async with Bot("123456:synthetic", session=session) as bot:
        await bot.get_updates()
        session.request_errors["getUpdates"] = RuntimeError("synthetic poll failure")
        for attempt_time in (101.0, 104.0):
            now[0] = attempt_time
            with pytest.raises(RuntimeError):
                await bot.get_updates()
            assert not watch.failed()
        now[0] = 107.0
        assert watch.failed()
        assert not watch.healthy()


async def test_backpressure_is_healthy_and_keeps_writing_heartbeats(  # noqa: PLR0915 - One SDK/heartbeat lifecycle.
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    watch = PollingWatch(failure_seconds=0.03)
    watch.start()
    session = FakeTelegramSession()
    session.middleware(watch)

    dispatcher = Dispatcher(disable_fsm=True)
    entered, finish, all_dispatched = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def handle(message: Message) -> None:
        entered.set()
        if message.message_id == 6:
            all_dispatched.set()
        await finish.wait()

    dispatcher.message.register(handle)
    lock = PollingLock(Mock(spec=Redis), "synthetic")
    lock.acquired_at = asyncio.get_running_loop().time()
    keeper = LockKeeper(lock)
    writes: list[float] = []

    async def heartbeat_write() -> bool:
        writes.append(asyncio.get_running_loop().time())
        return True

    monkeypatch.setattr(lock, "write_heartbeat", heartbeat_write)
    async with Bot("123456:synthetic", session=session) as bot:
        await bot.me()
        session.responses.append(
            (
                200,
                json.dumps(
                    {
                        "ok": True,
                        "result": [
                            message_update(i, "Buy milk").model_dump(mode="json", exclude_none=True)
                            for i in range(1, 7)
                        ],
                    }
                ),
            )
        )
        polling = asyncio.create_task(
            dispatcher.start_polling(
                bot, handle_signals=False, close_bot_session=False, tasks_concurrency_limit=2
            )
        )
        heartbeat = asyncio.create_task(
            HeartbeatTask(
                lock, keeper, healthy=watch.healthy, failed=watch.failed, interval_seconds=0.01
            ).run()
        )
        try:
            async with asyncio.timeout(2):
                await entered.wait()
                await asyncio.sleep(0.1)
                assert session.methods.count("getUpdates") == 1
                assert not watch.failed()
                assert watch.healthy()
                assert len(writes) >= 3
                assert not heartbeat.done()
        finally:
            finish.set()
            async with asyncio.timeout(2):
                await all_dispatched.wait()
            await dispatcher.stop_polling()
            await polling
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)


def test_token_filter_handles_a_token_bearing_telegram_network_error() -> None:
    upstream = ClientConnectionError("https://api.telegram.org/bot123456:synthetic/getUpdates")
    error = TelegramNetworkError(method=GetUpdates(), message=str(upstream))
    record = logging.LogRecord("synthetic", logging.ERROR, "test", 1, "%s", (error,), None)
    TokenRedactionFilter("123456:synthetic").filter(record)
    assert "123456:synthetic" not in logging.Formatter().format(record)


def test_production_lease_timings_leave_a_timeout_margin() -> None:
    assert LOCK_STOP_AFTER_SECONDS + REDIS_CALL_TIMEOUT_SECONDS < LOCK_TTL_SECONDS
    assert LOCK_RENEW_SECONDS < LOCK_STOP_AFTER_SECONDS
    assert LOCK_STOP_AFTER_SECONDS % LOCK_RENEW_SECONDS == 0


async def test_keeper_uses_fixed_slots_and_stops_within_the_conservative_bound(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class Clock:
        now = 100.0

        def time(self) -> float:
            return self.now

        async def sleep(self, delay: float) -> None:
            self.now += delay

    clock = Clock()
    lock = bot_lock.PollingLock(Mock(spec=Redis), "synthetic", call_timeout=0.04)
    lock.acquired_at = clock.now
    starts: list[float] = []

    async def renew() -> None:
        starts.append(clock.now)
        if len(starts) <= 2:
            clock.now += 0.12
        else:
            clock.now += 0.04
            if len(starts) > 10:
                raise AssertionError("keeper did not stop")
            raise ConnectionError("private Redis error")

    monkeypatch.setattr(lock, "renew", renew)
    monkeypatch.setattr(
        bot_lock, "asyncio", SimpleNamespace(get_running_loop=lambda: clock, sleep=clock.sleep)
    )
    keeper = bot_lock.LockKeeper(lock, renew_seconds=0.05, stop_after_seconds=0.15)
    await keeper.run()
    assert starts == pytest.approx([100.05, 100.20, 100.35])
    assert keeper.lost
    assert keeper.last_renewed == starts[1]
    assert clock.now <= starts[1] + 0.15 + lock.call_timeout + 0.005
    assert "ConnectionError" in caplog.text
    assert "private Redis error" not in caplog.text
