import asyncio
import socket
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from alembic import command

from tests.databases import (
    clean_test_database,
    drop_abandoned_databases,
    make_alembic_config,
    test_database,
)
from tests.integration.settings import InfraTestSettings


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
