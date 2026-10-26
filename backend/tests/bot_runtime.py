import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import pytest
from aiogram import Bot
from celery import Celery
from pydantic import PostgresDsn, RedisDsn, SecretStr
from redis.asyncio import Redis
from sqlalchemy import Engine, event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from taskbot.domain.extraction import Extraction
from taskbot.domain.text import MessageText
from taskbot.domain.voice_jobs import TranscriptionProvider
from taskbot.entrypoints.bot import __main__ as bot_cli
from taskbot.infrastructure.celery.app import create_celery_app
from taskbot.infrastructure.celery.job_queue import CeleryJobQueue
from taskbot.infrastructure.extraction.heuristic import HeuristicExtractor
from taskbot.infrastructure.redis.bot_lock import HeartbeatTask, LockKeeper, PollingLock
from taskbot.infrastructure.redis.client import create_redis_client
from taskbot.infrastructure.settings.bot import BotSettings
from taskbot.infrastructure.sqlalchemy.engine import create_engine_and_sessions
from tests.fakes.telegram_session import FakeTelegramSession
from tests.redis_sessions import RedisSession


class BotSession(FakeTelegramSession):
    def __init__(self, closed: list[str]) -> None:
        super().__init__()
        self.updates: list[dict[str, object]] = []
        self.stop_after_sends = 0
        self.stop_on_poll = False
        self.control: bot_cli.StopController | None = None
        self._closed = closed
        self._sent = 0

    def _default(self, name: str) -> tuple[int, str]:
        if name == "getUpdates" and self.updates:
            updates, self.updates = self.updates, []
            return 200, json.dumps({"ok": True, "result": updates})
        if name == "sendMessage":
            self._sent += 1
        if self.control is not None and (
            (name == "sendMessage" and self._sent == self.stop_after_sends)
            or (name == "getUpdates" and self.stop_on_poll)
        ):
            asyncio.get_running_loop().call_soon(self.control.signal)
        return super()._default(name)

    async def close(self) -> None:
        self._closed.append("session")
        await super().close()


class CountingExtractor:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def extract(self, text: MessageText) -> Extraction:
        self.texts.append(text.value)
        return await HeuristicExtractor().extract(text)


@dataclass
class BotHarness:
    settings: BotSettings
    closed: list[str] = field(default_factory=list)
    builds: list[str] = field(default_factory=list)
    extractor: CountingExtractor = field(default_factory=CountingExtractor)
    lock: PollingLock | None = None
    session: BotSession = field(init=False)

    def __post_init__(self) -> None:
        self.session = BotSession(self.closed)


class BotFactories:
    def __init__(self, owned: BotHarness, monkeypatch: pytest.MonkeyPatch) -> None:
        self.owned = owned
        self.monkeypatch = monkeypatch

    def make_engine(self, url: str) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
        self.owned.builds.append("engine")
        engine, sessions = create_engine_and_sessions(url)

        def disposed(sync_engine: Engine) -> None:
            self.owned.closed.append("engine")

        event.listen(engine.sync_engine, "engine_disposed", disposed)
        return engine, sessions

    def make_redis(self, url: str, *, client_name: str | None = None) -> Redis:
        self.owned.builds.append("redis")
        assert client_name == "bot"
        client = create_redis_client(url, client_name=client_name)
        close = client.aclose

        async def close_client() -> None:
            self.owned.closed.append("redis")
            await close()

        self.monkeypatch.setattr(client, "aclose", close_client)
        return client

    def make_lock(self, client: Redis, namespace: str) -> PollingLock:
        self.owned.lock = PollingLock(client, namespace, ttl_seconds=0.5, call_timeout=0.1)
        return self.owned.lock

    def make_keeper(self, lock: PollingLock) -> LockKeeper:
        return LockKeeper(lock, renew_seconds=0.05, stop_after_seconds=0.25)

    def make_app(self, *, broker_url: str, namespace: str) -> Celery:
        self.owned.builds.append("app")
        app = create_celery_app(broker_url=broker_url, namespace=namespace)
        close = app.close

        def close_app() -> None:
            self.owned.closed.append("app")
            close()

        self.monkeypatch.setattr(app, "close", close_app)
        return app

    def make_queue(self, app: Celery) -> CeleryJobQueue:
        self.owned.builds.append("queue")
        queue = CeleryJobQueue(app)
        close = queue.close

        def close_queue() -> None:
            self.owned.closed.append("queue")
            close()

        self.monkeypatch.setattr(queue, "close", close_queue)
        return queue

    @asynccontextmanager
    async def extractor(
        self, settings: BotSettings, *, deadline_seconds: float
    ) -> AsyncIterator[CountingExtractor]:
        assert deadline_seconds == 8
        self.owned.builds.append("extractor")
        try:
            yield self.owned.extractor
        finally:
            self.owned.closed.append("extractor")

    def make_bot(self, token: str) -> Bot:
        self.owned.builds.append("session")
        return Bot(token, session=self.owned.session)

    def capture_control(self, control: bot_cli.StopController) -> None:
        self.owned.session.control = control

    def heartbeat(
        self,
        lock: PollingLock,
        keeper: LockKeeper,
        *,
        healthy: Callable[[], bool],
        failed: Callable[[], bool],
    ) -> HeartbeatTask:
        return HeartbeatTask(lock, keeper, healthy=healthy, failed=failed, interval_seconds=0.02)


def configure_bot(
    database_url: str,
    redis_session: RedisSession,
    monkeypatch: pytest.MonkeyPatch,
) -> BotHarness:
    monkeypatch.setenv("TASKBOT_ENV_FILE", "/dev/null")
    settings = BotSettings(
        _env_file=None,
        database_url=PostgresDsn(database_url),
        redis_url=RedisDsn(redis_session.redis_url),
        celery_broker_url=RedisDsn(redis_session.broker_url),
        redis_namespace=redis_session.child(),
        telegram_bot_token=SecretStr("123456:synthetic"),
        task_extractor="heuristic",
        transcription_provider=TranscriptionProvider.FAKE,
    )
    owned = BotHarness(settings)

    factories = BotFactories(owned, monkeypatch)
    monkeypatch.setattr(bot_cli, "create_engine_and_sessions", factories.make_engine)
    monkeypatch.setattr(bot_cli, "create_redis_client", factories.make_redis)
    monkeypatch.setattr(bot_cli, "PollingLock", factories.make_lock)
    monkeypatch.setattr(bot_cli, "LockKeeper", factories.make_keeper)
    monkeypatch.setattr(bot_cli, "create_celery_app", factories.make_app)
    monkeypatch.setattr(bot_cli, "CeleryJobQueue", factories.make_queue)
    monkeypatch.setattr(bot_cli, "open_extractor", factories.extractor)
    monkeypatch.setattr(bot_cli, "Bot", factories.make_bot)
    monkeypatch.setattr(bot_cli, "_install_signals", factories.capture_control)
    monkeypatch.setattr(bot_cli, "HeartbeatTask", factories.heartbeat)
    return owned
