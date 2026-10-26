import os
from pathlib import Path

import pytest
from pydantic import PostgresDsn
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from taskbot.application.dashboard_token import AuthenticateToken
from taskbot.domain.errors import InvalidTokenError
from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.entrypoints.seed import build_seed_ports, seed
from taskbot.infrastructure.settings.seed import SeedSettings
from taskbot.infrastructure.sqlalchemy.engine import create_engine_and_sessions
from taskbot.infrastructure.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from taskbot.infrastructure.tokens import Sha256TokenHasher
from tests.databases import test_database as database_context
from tests.processes import run_process

ROOT = Path(__file__).resolve().parents[3]
MESSAGE = "Позвонить маме. Спросить про выходные."


async def test_seed_rotates_tokens_and_handles_duplicate_messages(
    postgres_uows: UnitOfWorkFactory,
) -> None:
    settings = SeedSettings(
        _env_file=None, database_url=PostgresDsn("postgresql+asyncpg://unused/test")
    )
    ports = build_seed_ports(settings, postgres_uows)
    first = await seed(ports, 1001, MESSAGE, -1)
    assert first.titles == (MESSAGE,)
    assert not first.duplicate
    assert first.link == f"http://localhost:5173/#token={first.token}"
    assert [event.type for event in first.events] == ["task.created", "auth.revoked"]
    assert first.token not in repr(first)
    authenticate = AuthenticateToken(postgres_uows, Sha256TokenHasher())
    assert (await authenticate.execute(first.token)).token_version == 1
    second = await seed(ports, 1001, MESSAGE, -1)
    assert second.duplicate
    assert second.titles == ()
    assert [event.type for event in second.events] == ["auth.revoked"]
    assert first.token != second.token
    with pytest.raises(InvalidTokenError):
        await authenticate.execute(first.token)
    assert (await authenticate.execute(second.token)).token_version == 2


def uows_from(sessions: async_sessionmaker[AsyncSession]) -> UnitOfWorkFactory:
    return lambda: SqlAlchemyUnitOfWork(sessions)


async def test_root_seed_command_migrates_and_rotates_on_a_throwaway_database() -> None:
    async with database_context() as url:
        env = dict(os.environ, DATABASE_URL=url, TASK_EXTRACTOR="heuristic")
        migrate = await run_process("yarn", "--silent", "db:migrate", cwd=ROOT, env=env)
        assert migrate.returncode == 0, migrate.stderr
        tokens: list[str] = []
        for _ in range(2):
            result = await run_process(
                "yarn", "--silent", "dev:seed", "1001", MESSAGE, cwd=ROOT, env=env
            )
            assert result.returncode == 0, result.stderr
            assert f"Title: {MESSAGE}" in result.stdout
            token = next(
                line.removeprefix("Token: ")
                for line in result.stdout.splitlines()
                if line.startswith("Token: ")
            )
            assert f"/#token={token}" in result.stdout
            assert "Older dashboard links stop working" in result.stdout
            assert "events not published (no publisher until S3)" in result.stderr
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
