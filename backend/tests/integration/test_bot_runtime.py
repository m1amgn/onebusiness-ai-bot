import asyncio
import json
import logging
import os
import signal
from collections import Counter

import pytest
from aiogram import Dispatcher
from aiogram.exceptions import TelegramConflictError
from aiogram.methods import GetUpdates
from aiohttp import ClientConnectionError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from taskbot.domain.voice_jobs import TranscriptionProvider
from taskbot.entrypoints.bot import BotPorts
from taskbot.entrypoints.bot import __main__ as bot_cli
from taskbot.entrypoints.bot.handlers import create_dispatcher
from taskbot.entrypoints.bot.runtime import InFlightUpdates, PollingWatch
from taskbot.infrastructure.redis.bot_lock import HeartbeatTask, LockKeeper, PollingLock
from taskbot.infrastructure.redis.client import create_redis_client
from tests.bot import message_update
from tests.bot_runtime import BotHarness, configure_bot
from tests.redis_sessions import RedisSession

REAL_INSTALL_SIGNALS = bot_cli._install_signals


@pytest.fixture
def bot_harness(
    database_url: str,
    clean_database: None,
    redis_session: RedisSession,
    monkeypatch: pytest.MonkeyPatch,
) -> BotHarness:
    return configure_bot(database_url, redis_session, monkeypatch)


async def test_two_messages_share_owned_clients_and_close_in_order(
    bot_harness: BotHarness, sql_sessions: async_sessionmaker[AsyncSession]
) -> None:
    owned = bot_harness
    owned.session.updates = [
        message_update(i, content).model_dump(mode="json", exclude_none=True)
        for i, content in [(1, "Buy milk"), (2, "Call home")]
    ]
    owned.session.stop_after_sends = 2
    async with asyncio.timeout(10):
        assert await bot_cli.run(owned.settings) == 0
    assert Counter(owned.builds) == Counter(
        {name: 1 for name in ("engine", "redis", "app", "queue", "extractor", "session")}
    )
    assert sorted(owned.extractor.texts) == ["Buy milk", "Call home"]
    assert owned.closed == ["session", "extractor", "queue", "app", "engine", "redis"]
    async with sql_sessions() as session:
        assert await session.scalar(text("SELECT count(*) FROM tasks")) == 2


async def test_startup_delay_keeps_ownership_past_the_original_lease(
    bot_harness: BotHarness,
) -> None:
    owned = bot_harness
    gate = asyncio.Event()
    owned.session.request_gates["getMe"] = gate
    owned.session.stop_on_poll = True
    task = asyncio.create_task(bot_cli.run(owned.settings))
    try:
        async with asyncio.timeout(10):
            await owned.session.started.wait()
            await asyncio.sleep(2.3)
            async with create_redis_client(str(owned.settings.redis_url)) as client:
                assert not await PollingLock(client, owned.settings.redis_namespace).acquire()
            gate.set()
            assert await task == 0
    finally:
        gate.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_loss_during_startup_never_sends_getupdates(bot_harness: BotHarness) -> None:
    owned = bot_harness
    gate = asyncio.Event()
    owned.session.request_gates["getMe"] = gate
    task = asyncio.create_task(bot_cli.run(owned.settings))
    try:
        async with asyncio.timeout(10):
            await owned.session.started.wait()
            assert owned.lock is not None
            async with create_redis_client(str(owned.settings.redis_url)) as client:
                await client.delete(owned.lock.key)
            assert await task == 1
        assert "getUpdates" not in owned.session.methods
        assert owned.session.closed
    finally:
        gate.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_poll_failure_and_unexpected_keeper_end_stop_the_bot(
    bot_harness: BotHarness, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    owned = bot_harness
    monkeypatch.setattr(bot_cli, "PollingWatch", lambda: PollingWatch(failure_seconds=0.06))
    owned.session.request_errors["getUpdates"] = TelegramConflictError(
        method=GetUpdates(), message="synthetic conflict"
    )
    async with asyncio.timeout(10):
        assert await bot_cli.run(owned.settings) == 1
    assert owned.session.closed
    assert (
        capsys.readouterr().err.strip()
        == "Telegram polling has failed for 60 s (TelegramConflictError)."
    )


async def test_unexpected_keeper_failure_cancels_startup(
    bot_harness: BotHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail(self: LockKeeper) -> None:
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(LockKeeper, "run", fail)
    async with asyncio.timeout(10):
        assert await bot_cli.run(bot_harness.settings) == 1
    assert "getUpdates" not in bot_harness.session.methods


async def test_loss_in_startup_hook_prevents_the_first_poll(
    bot_harness: BotHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    def dispatcher(ports: BotPorts, in_flight: InFlightUpdates) -> Dispatcher:
        result = create_dispatcher(ports, in_flight)

        async def delete_lock() -> None:
            assert bot_harness.lock is not None
            async with create_redis_client(str(bot_harness.settings.redis_url)) as client:
                await client.delete(bot_harness.lock.key)

        result.startup.register(delete_lock)
        return result

    monkeypatch.setattr(bot_cli, "create_dispatcher", dispatcher)
    async with asyncio.timeout(10):
        assert await bot_cli.run(bot_harness.settings) == 1
    assert "getUpdates" not in bot_harness.session.methods


@pytest.mark.parametrize(
    ("status", "description", "expected"),
    [
        (401, "Unauthorized", "Telegram rejected TELEGRAM_BOT_TOKEN."),
        (500, "Server unavailable", "Cannot reach api.telegram.org."),
    ],
)
async def test_telegram_startup_refusal_is_safe(
    bot_harness: BotHarness,
    status: int,
    description: str,
    expected: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bot_harness.session.responses.append(
        (status, json.dumps({"ok": False, "error_code": status, "description": description}))
    )
    assert await bot_cli.run(bot_harness.settings) == 1
    error = capsys.readouterr().err
    assert expected in error
    assert "123456:synthetic" not in error
    assert "getUpdates" not in bot_harness.session.methods


async def test_unexpected_heartbeat_failure_stops_the_bot(
    bot_harness: BotHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail(self: HeartbeatTask) -> None:
        raise RuntimeError("synthetic heartbeat failure")

    monkeypatch.setattr(HeartbeatTask, "run", fail)
    async with asyncio.timeout(10):
        assert await bot_cli.run(bot_harness.settings) == 1
    assert bot_harness.session.closed


async def test_missing_openai_key_is_warned_once_at_start(
    bot_harness: BotHarness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    settings = bot_harness.settings.model_copy(
        update={"transcription_provider": TranscriptionProvider.OPENAI, "openai_api_key": None}
    )
    bot_harness.session.stop_on_poll = True
    async with asyncio.timeout(10):
        assert await bot_cli.run(settings) == 0
    assert caplog.messages.count("OpenAI transcription is unavailable: missing API key") == 1


@pytest.mark.parametrize(
    "stop", ["finish", "second_signal", "deadline", "os_signal", "os_second_signal"]
)
async def test_root_drains_inflight_handlers_before_closing_session(
    bot_harness: BotHarness,
    monkeypatch: pytest.MonkeyPatch,
    stop: str,
) -> None:
    owned = bot_harness
    owned.extractor.gate = asyncio.Event()
    owned.session.updates = [
        message_update(1, "Buy milk").model_dump(mode="json", exclude_none=True)
    ]
    monkeypatch.setattr(bot_cli, "DRAIN_SECONDS", 0.15 if stop == "deadline" else 2)
    if stop in {"os_signal", "os_second_signal"}:
        monkeypatch.setattr(bot_cli, "_install_signals", REAL_INSTALL_SIGNALS)
    task = asyncio.create_task(bot_cli.run(owned.settings))
    try:
        async with asyncio.timeout(10):
            await owned.extractor.entered.wait()
            if stop in {"os_signal", "os_second_signal"}:
                os.kill(os.getpid(), signal.SIGTERM)
            else:
                assert owned.session.control is not None
                owned.session.control.signal()
            await asyncio.sleep(0.05)
            assert not task.done()
            assert not owned.session.closed
            assert not owned.extractor.cancelled.is_set()
            if stop in {"finish", "os_signal"}:
                owned.extractor.gate.set()
            elif stop == "os_second_signal":
                os.kill(os.getpid(), signal.SIGTERM)
            elif stop == "second_signal":
                assert owned.session.control is not None
                owned.session.control.signal()
            assert await task == 0
        assert owned.extractor.cancelled.is_set() == (
            stop in {"second_signal", "os_second_signal", "deadline"}
        )
        assert ("sendMessage" in owned.session.methods) == (stop in {"finish", "os_signal"})
        assert owned.closed == ["session", "extractor", "queue", "app", "engine", "redis"]
    finally:
        owned.extractor.gate.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_signal_during_startup_releases_lock_without_polling(bot_harness: BotHarness) -> None:
    owned = bot_harness
    owned.session.request_gates["getMe"] = asyncio.Event()
    task = asyncio.create_task(bot_cli.run(owned.settings))
    try:
        async with asyncio.timeout(10):
            await owned.session.started.wait()
            assert owned.session.control is not None
            owned.session.control.signal()
            assert await task == 0
        assert "getUpdates" not in owned.session.methods
        assert owned.lock is not None
        async with create_redis_client(str(owned.settings.redis_url)) as client:
            assert not await client.exists(owned.lock.key)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_root_caps_concurrent_updates_at_ten(bot_harness: BotHarness) -> None:
    owned = bot_harness
    owned.extractor.gate = asyncio.Event()
    owned.session.updates = [
        message_update(i, f"Task {i}").model_dump(mode="json", exclude_none=True)
        for i in range(1, 12)
    ]
    task = asyncio.create_task(bot_cli.run(owned.settings))
    try:
        async with asyncio.timeout(10):
            await owned.extractor.ten_active.wait()
            await asyncio.sleep(0.05)
            assert owned.extractor.active == 10
            assert len(owned.extractor.texts) == 10
            owned.session.stop_after_sends = 11
            owned.extractor.gate.set()
            assert await task == 0
        assert len(owned.extractor.texts) == 11
        assert owned.extractor.maximum_active == 10
    finally:
        owned.extractor.gate.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_external_cancel_after_stop_request_still_propagates(
    bot_harness: BotHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = bot_harness
    owned.extractor.gate = asyncio.Event()
    owned.session.updates = [
        message_update(1, "Buy milk").model_dump(mode="json", exclude_none=True)
    ]
    monkeypatch.setattr(bot_cli, "DRAIN_SECONDS", 2)
    task = asyncio.create_task(bot_cli.run(owned.settings))
    try:
        async with asyncio.timeout(10):
            await owned.extractor.entered.wait()
            assert owned.session.control is not None
            owned.session.control.signal()
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    finally:
        owned.extractor.gate.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_first_signal_after_internal_failure_still_drains(bot_harness: BotHarness) -> None:
    owned = bot_harness
    owned.extractor.gate = asyncio.Event()
    owned.session.updates = [
        message_update(1, "Buy milk").model_dump(mode="json", exclude_none=True)
    ]
    task = asyncio.create_task(bot_cli.run(owned.settings))
    try:
        async with asyncio.timeout(10):
            await owned.extractor.entered.wait()
            assert owned.session.control is not None
            owned.session.control.fail("synthetic internal stop")
            owned.session.control.signal()
            await asyncio.sleep(0.05)
            assert not owned.extractor.cancelled.is_set()
            assert not owned.session.closed
            owned.extractor.gate.set()
            assert await task == 1
        assert "sendMessage" in owned.session.methods
    finally:
        owned.extractor.gate.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(
    "error", [TimeoutError("private timeout"), ClientConnectionError("private network error")]
)
async def test_transport_failure_at_getme_has_a_safe_exit(
    bot_harness: BotHarness, error: Exception, capsys: pytest.CaptureFixture[str]
) -> None:
    bot_harness.session.request_errors["getMe"] = error
    assert await bot_cli.run(bot_harness.settings) == 1
    assert capsys.readouterr().err.strip() == "Cannot reach api.telegram.org."
    assert "getUpdates" not in bot_harness.session.methods


async def test_keeper_failure_after_polling_has_the_lock_loss_exit(
    bot_harness: BotHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    owned = bot_harness
    owned.extractor.gate = asyncio.Event()
    owned.session.updates = [
        message_update(1, "Buy milk").model_dump(mode="json", exclude_none=True)
    ]
    task = asyncio.create_task(bot_cli.run(owned.settings))
    try:
        async with asyncio.timeout(10):
            await owned.extractor.entered.wait()
            assert owned.lock is not None
            async with create_redis_client(str(owned.settings.redis_url)) as client:
                await client.delete(owned.lock.key)
            owned.extractor.gate.set()
            assert await task == 1
        assert capsys.readouterr().err.strip() == "The polling lock was lost."
    finally:
        owned.extractor.gate.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_one_transient_startup_renewal_error_does_not_stop_polling(
    bot_harness: BotHarness, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    original = PollingLock.renew
    calls = 0

    async def renew(lock: PollingLock) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("private Redis reply")
        await original(lock)

    monkeypatch.setattr(PollingLock, "renew", renew)
    bot_harness.session.stop_on_poll = True
    assert await bot_cli.run(bot_harness.settings) == 0
    assert "getUpdates" in bot_harness.session.methods
    assert "TimeoutError" in caplog.text
    assert "private Redis reply" not in caplog.text


async def test_signal_during_ambiguous_acquire_still_releases_the_key(
    bot_harness: BotHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = PollingLock.acquire
    acquired = asyncio.Event()

    async def acquire(lock: PollingLock) -> bool:
        await original(lock)
        lock.acquired_at = None
        acquired.set()
        await asyncio.Event().wait()
        return True

    monkeypatch.setattr(PollingLock, "acquire", acquire)
    owned = bot_harness
    task = asyncio.create_task(bot_cli.run(owned.settings))
    try:
        async with asyncio.timeout(10):
            await acquired.wait()
            assert owned.session.control is not None
            owned.session.control.signal()
            assert await task == 0
        assert owned.lock is not None
        async with create_redis_client(str(owned.settings.redis_url)) as client:
            assert not await client.exists(owned.lock.key)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
