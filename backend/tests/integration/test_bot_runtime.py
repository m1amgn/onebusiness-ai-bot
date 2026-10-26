import asyncio
from collections import Counter
from datetime import UTC, datetime

import pytest
from aiogram.exceptions import TelegramConflictError
from aiogram.methods import GetUpdates
from aiogram.types import Chat, Message, Update, User
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from taskbot.entrypoints.bot import __main__ as bot_cli
from taskbot.entrypoints.bot.runtime import PollingWatch
from taskbot.infrastructure.redis.bot_lock import LockKeeper, PollingLock
from taskbot.infrastructure.redis.client import create_redis_client
from tests.bot_runtime import BotHarness, configure_bot
from tests.redis_sessions import RedisSession


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
        Update(
            update_id=i,
            message=Message(
                message_id=i,
                date=datetime.now(UTC),
                chat=Chat(id=7, type="private"),
                from_user=User(id=7, is_bot=False, first_name="Synthetic"),
                text=content,
            ),
        ).model_dump(mode="json", exclude_none=True)
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
            await asyncio.sleep(0.65)
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
    bot_harness: BotHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = bot_harness
    monkeypatch.setattr(bot_cli, "PollingWatch", lambda: PollingWatch(failure_seconds=0.06))
    owned.session.request_errors["getUpdates"] = TelegramConflictError(
        method=GetUpdates(), message="synthetic conflict"
    )
    async with asyncio.timeout(10):
        assert await bot_cli.run(owned.settings) == 1
    assert owned.session.closed


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
    from aiogram import Dispatcher

    from taskbot.entrypoints.bot import BotPorts
    from taskbot.entrypoints.bot.handlers import create_dispatcher
    from taskbot.entrypoints.bot.runtime import InFlightUpdates

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
    import json

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
    from taskbot.infrastructure.redis.bot_lock import HeartbeatTask

    async def fail(self: HeartbeatTask) -> None:
        raise RuntimeError("synthetic heartbeat failure")

    monkeypatch.setattr(HeartbeatTask, "run", fail)
    async with asyncio.timeout(10):
        assert await bot_cli.run(bot_harness.settings) == 1
    assert bot_harness.session.closed


async def test_missing_openai_key_is_warned_once_at_start(
    bot_harness: BotHarness, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    from taskbot.domain.voice_jobs import TranscriptionProvider

    caplog.set_level(logging.WARNING)
    settings = bot_harness.settings.model_copy(
        update={"transcription_provider": TranscriptionProvider.OPENAI, "openai_api_key": None}
    )
    bot_harness.session.stop_on_poll = True
    async with asyncio.timeout(10):
        assert await bot_cli.run(settings) == 0
    assert caplog.messages.count("OpenAI transcription is unavailable: missing API key") == 1
