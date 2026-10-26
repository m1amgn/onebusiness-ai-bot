import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import asyncpg
import pytest

from tests import databases
from tests.databases import (
    drop_abandoned_databases,
    maintenance_url,
    owned_database,
    raw_url,
)
from tests.databases import (
    test_database as database_context,
)
from tests.integration.settings import InfraTestSettings


async def test_plain_drop_protects_a_live_anchor_when_the_precheck_misses_it(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def missed_precheck(connection: asyncpg.Connection, name: str) -> bool:
        return False

    # Only bypass our optimization; the connection and DROP are real PostgreSQL.
    monkeypatch.setattr(databases, "has_live_connections", missed_precheck)
    caplog.set_level("INFO", logger="tests.databases")
    async with owned_database() as database:
        name = urlsplit(database.url).path.removeprefix("/")
        future = datetime.now(UTC) + timedelta(hours=7)
        assert await drop_abandoned_databases(future, names={name}) == []
        assert "Keeping live or concurrently removed test database" in caplog.text
        assert await database.anchor.fetchval("SELECT 1") == 1


async def test_live_owner_survives_age_filter_and_closed_owner_is_cleaned() -> None:
    async with database_context() as url:
        name = urlsplit(url).path.removeprefix("/")
        future = datetime.now(UTC) + timedelta(hours=7)
        assert await drop_abandoned_databases(future, names={name}) == []
        connection = await asyncpg.connect(raw_url(url))
        try:
            assert await connection.fetchval("SELECT 1") == 1
        finally:
            await connection.close()
    maintenance = await asyncpg.connect(maintenance_url(str(InfraTestSettings().database_url)))
    try:
        assert not await maintenance.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=$1)", name
        )
    finally:
        await maintenance.close()


async def test_two_stale_sweeps_tolerate_the_same_database() -> None:
    async with owned_database() as database:
        name = urlsplit(database.url).path.removeprefix("/")
        await database.anchor.close()
        future = datetime.now(UTC) + timedelta(hours=7)
        async with asyncio.TaskGroup() as group:
            a = group.create_task(drop_abandoned_databases(future, names={name}))
            b = group.create_task(drop_abandoned_databases(future, names={name}))
        assert set(a.result() + b.result()) == {name}


async def test_session_database_is_migrated(database_url: str, clean_database: None) -> None:
    connection = await asyncpg.connect(raw_url(database_url))
    try:
        assert await connection.fetchval("SELECT current_database()") == urlsplit(
            database_url
        ).path.removeprefix("/")
        assert await connection.fetchval("SELECT version_num FROM alembic_version") == "0002"
    finally:
        await connection.close()
