import json
import logging
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest
from pydantic import PostgresDsn
from redis.asyncio.client import PubSub
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from taskbot.application.dashboard_token import AuthenticateToken
from taskbot.domain.errors import InvalidTokenError
from taskbot.domain.events import DomainEvent
from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.entrypoints.seed import build_seed_ports, seed
from taskbot.infrastructure.redis.client import create_redis_client
from taskbot.infrastructure.settings.seed import SeedSettings
from taskbot.infrastructure.sqlalchemy.engine import create_engine_and_sessions
from taskbot.infrastructure.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from taskbot.infrastructure.tokens import Sha256TokenHasher
from tests.databases import test_database as database_context
from tests.fakes.messaging import FakeEventPublisher
from tests.processes import ProcessResult, run_process
from tests.redis_sessions import RedisSession

ROOT = Path(__file__).resolve().parents[3]
MESSAGE = "Позвонить маме. Спросить про выходные."


@dataclass
class CommittedPublisher:
    uows: UnitOfWorkFactory
    expected_version: int = 1
    events: list[DomainEvent] = field(default_factory=list)
    observations: list[tuple[int, int, int]] = field(default_factory=list)

    async def publish(self, event: DomainEvent) -> None:
        async with self.uows() as uow:
            user = await uow.users.get(event.user_id)
            tasks = await uow.tasks.list_for_user(event.user_id)
        assert user is not None
        assert user.telegram_user_id == 1001
        assert user.token_version == self.expected_version
        assert len(tasks) == 1
        self.observations.append((user.id, user.token_version, len(tasks)))
        self.events.append(event)


async def test_seed_rotates_tokens_and_handles_duplicate_messages(
    postgres_uows: UnitOfWorkFactory,
) -> None:
    settings = SeedSettings(
        _env_file=None, database_url=PostgresDsn("postgresql+asyncpg://unused/test")
    )
    publisher = CommittedPublisher(postgres_uows)
    ports = build_seed_ports(settings, postgres_uows, publisher=publisher)
    first = await seed(ports, 1001, MESSAGE, -1)
    assert first.titles == (MESSAGE,)
    assert not first.duplicate
    assert first.link == f"http://localhost:5173/#token={first.token}"
    assert [event.type for event in first.events] == ["task.created", "auth.revoked"]
    assert tuple(publisher.events) == first.events
    assert publisher.observations == [(first.user_id, 1, 1), (first.user_id, 1, 1)]
    assert first.user_id != 1001
    assert first.token not in repr(first)
    authenticate = AuthenticateToken(postgres_uows, Sha256TokenHasher())
    authenticated = await authenticate.execute(first.token)
    assert (authenticated.user_id, authenticated.token_version) == (first.user_id, 1)
    publisher.expected_version = 2
    publisher.events.clear()
    second = await seed(ports, 1001, MESSAGE, -1)
    assert second.duplicate
    assert second.titles == ()
    assert [event.type for event in second.events] == ["auth.revoked"]
    assert tuple(publisher.events) == second.events
    assert second.user_id == first.user_id
    assert publisher.observations[-1] == (second.user_id, 2, 1)
    assert first.token != second.token
    with pytest.raises(InvalidTokenError):
        await authenticate.execute(first.token)
    assert (await authenticate.execute(second.token)).token_version == 2


async def test_failed_seed_publication_preserves_committed_tasks_and_token(
    postgres_uows: UnitOfWorkFactory, caplog: pytest.LogCaptureFixture
) -> None:
    settings = SeedSettings(
        _env_file=None, database_url=PostgresDsn("postgresql+asyncpg://unused/test")
    )
    publisher = FakeEventPublisher(fail=True)
    with caplog.at_level(logging.ERROR, logger="taskbot.application.publishing"):
        result = await seed(
            build_seed_ports(settings, postgres_uows, publisher=publisher), 1001, MESSAGE, -1
        )
    assert publisher.calls.items == ["events.publish"]
    records = [
        record for record in caplog.records if record.name == "taskbot.application.publishing"
    ]
    assert len(records) == 1
    assert "skipped 1 events" in records[0].getMessage()
    assert result.link == f"http://localhost:5173/#token={result.token}"
    authenticate = AuthenticateToken(postgres_uows, Sha256TokenHasher())
    assert (await authenticate.execute(result.token)).user_id == result.user_id
    async with postgres_uows() as uow:
        tasks = await uow.tasks.list_for_user(result.user_id)
    assert [task.title for task in tasks] == [MESSAGE]


def uows_from(sessions: async_sessionmaker[AsyncSession]) -> UnitOfWorkFactory:
    return lambda: SqlAlchemyUnitOfWork(sessions)


async def run_seed_command(env: dict[str, str]) -> tuple[str, ProcessResult]:
    result = await run_process("yarn", "--silent", "dev:seed", "1001", MESSAGE, cwd=ROOT, env=env)
    token = next(
        (
            line.removeprefix("Token: ")
            for line in result.stdout.splitlines()
            if line.startswith("Token: ")
        ),
        "",
    )
    # Failed assertions must not echo even the short-lived test dashboard token.
    redacted = replace(result, stdout=result.stdout.replace(token, "[redacted]") if token else "")
    assert redacted.returncode == 0, redacted.stderr
    assert token
    assert f"Title: {MESSAGE}" in redacted.stdout
    assert "/#token=[redacted]" in redacted.stdout
    assert "Older dashboard links stop working" in redacted.stdout
    return token, redacted


async def assert_seed_events(sub: PubSub, channel: str) -> None:
    received = []
    for _ in range(2):
        message = await sub.get_message(ignore_subscribe_messages=True, timeout=2)
        assert message is not None
        assert message["channel"] == channel
        received.append(json.loads(message["data"]))
    assert [event["type"] for event in received] == ["task.created", "auth.revoked"]
    assert received[0]["task"]["title"] == MESSAGE


@pytest.mark.parametrize("redis_available", [True, False], ids=["published", "redis-down"])
async def test_root_seed_command_migrates_and_rotates_on_a_throwaway_database(
    redis_session: RedisSession, redis_available: bool
) -> None:
    namespace = redis_session.child()
    async with database_context() as url:
        env = dict(
            os.environ,
            TASKBOT_ENV_FILE="/dev/null",
            DATABASE_URL=url,
            REDIS_URL=redis_session.redis_url if redis_available else "redis://127.0.0.1:1/0",
            REDIS_NAMESPACE=namespace,
            TASK_EXTRACTOR="heuristic",
        )
        migrate = await run_process("yarn", "--silent", "db:migrate", cwd=ROOT, env=env)
        assert migrate.returncode == 0, migrate.stderr
        tokens: list[str] = []
        async with (
            create_redis_client(redis_session.redis_url) as observer,
            observer.pubsub() as sub,
        ):
            await sub.psubscribe(namespace + "events:user:*")
            acknowledgement = await sub.get_message(ignore_subscribe_messages=False, timeout=2)
            assert acknowledgement is not None
            assert acknowledgement["type"] == "psubscribe"
            for _ in range(2):
                token, result = await run_seed_command(env)
                channel = namespace + "events:user:1"
                assert f"Events channel: {channel}" in result.stdout
                if redis_available:
                    await assert_seed_events(sub, channel)
                    assert "Post-commit event publication failed" not in result.stderr
                else:
                    assert result.stderr.count("Post-commit event publication failed") == 1
                    assert "skipped 1 events" in result.stderr
                assert await sub.get_message(ignore_subscribe_messages=True, timeout=0.05) is None
                tokens.append(token)
        assert tokens[0] != tokens[1]
        engine, sessions = create_engine_and_sessions(url, pool_size=2)
        try:
            authenticate = AuthenticateToken(uows_from(sessions), Sha256TokenHasher())
            with pytest.raises(InvalidTokenError):
                await authenticate.execute(tokens[0])
            assert (await authenticate.execute(tokens[1])).token_version == 2
            async with SqlAlchemyUnitOfWork(sessions) as uow:
                tasks = await uow.tasks.list_for_user(1)
                assert len(tasks) == 2
                assert all(task.title == MESSAGE for task in tasks)
                assert tasks[0].inbound_message_id > tasks[1].inbound_message_id
        finally:
            await engine.dispose()
