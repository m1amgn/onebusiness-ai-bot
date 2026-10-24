import logging
import re
import secrets
import time
from collections.abc import AsyncIterator, Collection
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import asyncpg
from alembic.config import Config
from sqlalchemy.engine import make_url

from taskbot.domain.time import require_aware
from tests.integration.settings import InfraTestSettings

PATTERN = re.compile(r"taskbot_test_([0-9]+)_[0-9a-f]{8}")
logger = logging.getLogger(__name__)


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def raw_url(url: str) -> str:
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


def maintenance_url(url: str) -> str:
    return make_url(raw_url(url)).set(database="postgres").render_as_string(hide_password=False)


def is_test_database(name: str) -> bool:
    return len(name) <= 63 and PATTERN.fullmatch(name) is not None


def is_abandoned(name: str, now: datetime) -> bool:
    require_aware(now)
    match = PATTERN.fullmatch(name)
    return (
        is_test_database(name)
        and match is not None
        and int(match.group(1)) < now.timestamp() - 6 * 60 * 60
    )


def make_alembic_config(url: str) -> Config:
    name = make_url(url).database or ""
    if not is_test_database(name):
        raise ValueError("Migration tests require a test database")
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes["database_url"] = url
    return config


@dataclass(frozen=True)
class OwnedDatabase:
    url: str
    anchor: asyncpg.Connection


@asynccontextmanager
async def owned_database(name_time: int | None = None) -> AsyncIterator[OwnedDatabase]:
    source = str(InfraTestSettings().database_url)
    timestamp = int(time.time()) if name_time is None else name_time
    name = f"taskbot_test_{timestamp}_{secrets.token_hex(4)}"
    if not is_test_database(name):
        raise ValueError("Invalid test database name")
    maintenance = await asyncpg.connect(maintenance_url(source), timeout=15)
    anchor: asyncpg.Connection | None = None
    created = False
    try:
        await maintenance.execute(f"CREATE DATABASE {quote_identifier(name)}")
        created = True
        url = make_url(source).set(database=name).render_as_string(hide_password=False)
        anchor = await asyncpg.connect(raw_url(url), timeout=15)
        yield OwnedDatabase(url, anchor)
    finally:
        try:
            if anchor is not None:
                await anchor.close()
            if created:
                await maintenance.execute(
                    f"DROP DATABASE IF EXISTS {quote_identifier(name)} WITH (FORCE)"
                )
        finally:
            await maintenance.close()


@asynccontextmanager
# This is an async context manager for tests, not a pytest test function.
async def test_database(name_time: int | None = None) -> AsyncIterator[str]:  # noqa: PT028
    async with owned_database(name_time) as database:
        yield database.url


async def has_live_connections(connection: asyncpg.Connection, name: str) -> bool:
    return bool(
        await connection.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=$1)", name
        )
    )


async def drop_abandoned_databases(
    now: datetime, *, names: Collection[str] | None = None
) -> list[str]:
    require_aware(now)
    source = str(InfraTestSettings().database_url)
    connection = await asyncpg.connect(maintenance_url(source), timeout=15)
    dropped: list[str] = []
    try:
        rows = await connection.fetch(
            "SELECT datname FROM pg_database WHERE starts_with(datname,'taskbot_test_')"
        )
        for row in rows:
            name = str(row[0])
            if not is_abandoned(name, now) or (names is not None and name not in names):
                continue
            if await has_live_connections(connection, name):
                logger.info("Keeping test database with a live connection: %s", name)
                continue
            try:
                await connection.execute(f"DROP DATABASE IF EXISTS {quote_identifier(name)}")
                dropped.append(name)
            except asyncpg.PostgresError as error:
                if error.sqlstate not in {"55006", "3D000"}:
                    raise RuntimeError(f"Failed cleanup of test database {name}") from error
                logger.info("Keeping live or concurrently removed test database: %s", name)
        return dropped
    finally:
        await connection.close()


async def clean_test_database(url: str) -> None:
    expected = make_url(url).database or ""
    if not is_test_database(expected):
        raise ValueError("Cleanup requires a test database")
    connection = await asyncpg.connect(raw_url(url), timeout=15)
    try:
        async with connection.transaction():
            await connection.execute("SET LOCAL lock_timeout = '5s'")
            if await connection.fetchval("SELECT current_database()") != expected:
                raise RuntimeError("Unexpected cleanup database")
            tables = await connection.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                "AND tablename <> 'alembic_version'"
            )
            if tables:
                names = ", ".join(quote_identifier(str(row[0])) for row in tables)
                await connection.execute(f"TRUNCATE {names} RESTART IDENTITY CASCADE")
    finally:
        await connection.close()
