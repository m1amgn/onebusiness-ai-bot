import asyncio
import socket
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from alembic import command
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from taskbot.domain.ports.persistence import UnitOfWorkFactory
from tests.databases import (
    clean_test_database,
    drop_abandoned_databases,
    make_alembic_config,
    test_database,
)
from tests.integration.settings import InfraTestSettings
from tests.redis_sessions import RedisSession, drop_abandoned_namespaces, open_redis_session


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if item.path.is_relative_to(Path(__file__).parent / "integration"):
            item.add_marker(pytest.mark.integration)


def ensure_reachable(service: str, url: str, default_port: int) -> None:
    parsed = urlsplit(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or default_port
    try:
        with socket.create_connection((host, port), timeout=2):
            return
    except OSError:
        pytest.exit(f"{service} at {host}:{port} is unreachable: run yarn infra:up", returncode=2)


@pytest.hookimpl(trylast=True)
def pytest_collection_finish(session: pytest.Session) -> None:
    if session.config.option.collectonly:
        return
    if not any(item.get_closest_marker("integration") for item in session.items):
        return
    settings = InfraTestSettings()
    ensure_reachable("PostgreSQL", str(settings.database_url), 5432)
    ensure_reachable("Redis", str(settings.redis_url), 6379)


@pytest_asyncio.fixture(scope="session")
async def database_url() -> AsyncIterator[str]:
    await drop_abandoned_databases(datetime.now(UTC))
    async with test_database() as url:
        await asyncio.to_thread(command.upgrade, make_alembic_config(url), "head")
        yield url


@pytest_asyncio.fixture
async def clean_database(database_url: str) -> None:
    await clean_test_database(database_url)


@pytest_asyncio.fixture(scope="session")
async def sql_sessions(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    from taskbot.infrastructure.sqlalchemy.engine import create_engine_and_sessions

    engine, sessions = create_engine_and_sessions(database_url, pool_size=2)
    try:
        yield sessions
    finally:
        await engine.dispose()


@pytest.fixture
def postgres_uows(
    sql_sessions: async_sessionmaker[AsyncSession], clean_database: None
) -> UnitOfWorkFactory:
    from taskbot.infrastructure.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

    return lambda: SqlAlchemyUnitOfWork(sql_sessions)


@pytest_asyncio.fixture(scope="session")
async def redis_session() -> AsyncIterator[RedisSession]:
    await drop_abandoned_namespaces(datetime.now(UTC))
    async with open_redis_session() as session:
        yield session
