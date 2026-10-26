import json
import logging
import os
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

import pytest
from pydantic import PostgresDsn
from redis.asyncio.client import PubSub
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from taskbot.application.dashboard_token import AuthenticateToken
from taskbot.domain.errors import DomainError, InvalidTokenError
from taskbot.domain.events import DomainEvent
from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.domain.users import TelegramIdentity
from taskbot.entrypoints import seed as seed_entrypoint
from taskbot.entrypoints.seed import build_seed_ports, seed
from taskbot.infrastructure.extraction.heuristic import HeuristicExtractor
from taskbot.infrastructure.redis.client import create_redis_client
from taskbot.infrastructure.settings.seed import SeedSettings
from taskbot.infrastructure.sqlalchemy.engine import create_engine_and_sessions
from taskbot.infrastructure.sqlalchemy.tables import UserRow
from taskbot.infrastructure.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from taskbot.infrastructure.tokens import Sha256TokenHasher
from tests.contract.helpers import all_tasks
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
            tasks = await all_tasks(uow, event.user_id)
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
    ports = build_seed_ports(settings, postgres_uows, HeuristicExtractor(), publisher=publisher)
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
            build_seed_ports(settings, postgres_uows, HeuristicExtractor(), publisher=publisher),
            1001,
            MESSAGE,
            -1,
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
        tasks = await all_tasks(uow, result.user_id)
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
                tasks = await all_tasks(uow, 1)
                assert len(tasks) == 2
                assert all(task.title == MESSAGE for task in tasks)
                assert tasks[0].inbound_message_id > tasks[1].inbound_message_id
        finally:
            await engine.dispose()


async def test_keep_token_seed_commits_tasks_without_changing_dashboard_token(
    postgres_uows: UnitOfWorkFactory, sql_sessions: async_sessionmaker[AsyncSession]
) -> None:
    settings = SeedSettings(
        _env_file=None, database_url=PostgresDsn("postgresql+asyncpg://unused/test")
    )
    publisher = CommittedPublisher(postgres_uows)
    ports = build_seed_ports(settings, postgres_uows, HeuristicExtractor(), publisher=publisher)
    issued = await ports.issue_token.execute(TelegramIdentity(1001, "Seed user"))
    statement = select(UserRow.token_version, UserRow.dashboard_token_hash).where(
        UserRow.id == issued.user_id
    )
    async with sql_sessions() as session:
        before = (await session.execute(statement)).one()
    result = await seed_entrypoint.seed_keep_token(ports, 1001, MESSAGE, -1)
    async with sql_sessions() as session:
        after = (await session.execute(statement)).one()
    assert bool(before == after)
    assert before.token_version == 1
    assert before.dashboard_token_hash is not None
    assert result.user_id == issued.user_id
    assert result.titles == (MESSAGE,)
    assert not result.duplicate
    assert [event.type for event in result.events] == ["task.created"]
    assert tuple(publisher.events) == result.events
    assert publisher.observations == [(issued.user_id, 1, 1)]
    assert tuple(field.name for field in fields(result)) == (
        "user_id",
        "titles",
        "duplicate",
        "events",
    )
    authenticate = AuthenticateToken(postgres_uows, Sha256TokenHasher())
    authenticated = await authenticate.execute(issued.token)
    assert (authenticated.user_id, authenticated.token_version) == (issued.user_id, 1)


async def test_keep_token_seed_duplicate_publishes_nothing(
    postgres_uows: UnitOfWorkFactory,
) -> None:
    settings = SeedSettings(
        _env_file=None, database_url=PostgresDsn("postgresql+asyncpg://unused/test")
    )
    publisher = FakeEventPublisher()
    ports = build_seed_ports(settings, postgres_uows, HeuristicExtractor(), publisher=publisher)
    issued = await ports.issue_token.execute(TelegramIdentity(1001, "Seed user"))
    first = await seed_entrypoint.seed_keep_token(ports, 1001, MESSAGE, -1)
    publisher.events.clear()
    publisher.calls.items.clear()
    duplicate = await seed_entrypoint.seed_keep_token(ports, 1001, MESSAGE, -1)
    assert duplicate.user_id == first.user_id
    assert duplicate.duplicate
    assert duplicate.titles == ()
    assert duplicate.events == ()
    assert publisher.events == []
    assert publisher.calls.items == []
    async with postgres_uows() as uow:
        assert await uow.users.get_token_version(issued.user_id) == 1
        assert len(await all_tasks(uow, issued.user_id)) == 1
    authenticate = AuthenticateToken(postgres_uows, Sha256TokenHasher())
    assert (await authenticate.execute(issued.token)).user_id == first.user_id


@pytest.mark.parametrize("already_registered", [False, True], ids=["unknown", "registered"])
async def test_keep_token_seed_refuses_users_without_a_dashboard_link(
    postgres_uows: UnitOfWorkFactory, already_registered: bool
) -> None:
    settings = SeedSettings(
        _env_file=None, database_url=PostgresDsn("postgresql+asyncpg://unused/test")
    )
    publisher = FakeEventPublisher()
    ports = build_seed_ports(settings, postgres_uows, HeuristicExtractor(), publisher=publisher)
    identity = TelegramIdentity(1001, "Seed user")
    if already_registered:
        await ports.register.execute(identity)
    with pytest.raises(
        DomainError,
        match=r"^This user has no dashboard link yet; run without --keep-token first\.$",
    ):
        await seed_entrypoint.seed_keep_token(ports, 1001, MESSAGE, -1)
    registered = await ports.register.execute(identity)
    assert not registered.created
    async with postgres_uows() as uow:
        assert await uow.users.get_token_version(registered.user.id) == 0
        assert await all_tasks(uow, registered.user.id) == ()
    assert publisher.events == []
    assert publisher.calls.items == []


@pytest.mark.parametrize("flag_first", [True, False], ids=["before-id", "after-id"])
async def test_root_keep_token_seed_command_preserves_the_dashboard_session(
    postgres_uows: UnitOfWorkFactory,
    database_url: str,
    redis_session: RedisSession,
    flag_first: bool,
) -> None:
    namespace = redis_session.child()
    settings = SeedSettings(_env_file=None, database_url=PostgresDsn(database_url))
    ports = build_seed_ports(
        settings, postgres_uows, HeuristicExtractor(), publisher=FakeEventPublisher()
    )
    issued = await ports.issue_token.execute(TelegramIdentity(1001, "Seed user"))
    env = dict(
        os.environ,
        TASKBOT_ENV_FILE="/dev/null",
        DATABASE_URL=database_url,
        REDIS_URL=redis_session.redis_url,
        REDIS_NAMESPACE=namespace,
        TASK_EXTRACTOR="heuristic",
    )
    arguments = ["--keep-token", "1001"] if flag_first else ["1001", "--keep-token"]
    async with (
        create_redis_client(redis_session.redis_url) as observer,
        observer.pubsub() as sub,
    ):
        channel = namespace + f"events:user:{issued.user_id}"
        await sub.subscribe(channel)
        assert await sub.get_message(ignore_subscribe_messages=False, timeout=2) is not None
        result = await run_process(
            "yarn", "--silent", "dev:seed", *arguments, MESSAGE, cwd=ROOT, env=env
        )
        assert result.returncode == 0, result.stderr
        assert f"Title: {MESSAGE}\n" in result.stdout
        assert f"Events channel: {channel}\n" in result.stdout
        assert "Token unchanged; open dashboards stay signed in.\n" in result.stdout
        assert "Token:" not in result.stdout
        assert "Link:" not in result.stdout
        assert "#token=" not in result.stdout
        event = await sub.get_message(ignore_subscribe_messages=True, timeout=2)
        assert event is not None
        assert json.loads(event["data"])["type"] == "task.created"
        assert await sub.get_message(ignore_subscribe_messages=True, timeout=0.05) is None
    authenticate = AuthenticateToken(postgres_uows, Sha256TokenHasher())
    assert (await authenticate.execute(issued.token)).token_version == 1
