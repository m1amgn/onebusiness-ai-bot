from urllib.parse import urlsplit

import asyncpg
from redis.asyncio import Redis

from tests.integration.settings import InfraTestSettings


async def test_postgresql_credentials_database_and_version() -> None:
    settings = InfraTestSettings()
    url = str(settings.database_url).replace("postgresql+asyncpg://", "postgresql://", 1)
    connection = await asyncpg.connect(url)
    try:
        row = await connection.fetchrow(
            "SELECT current_database() AS database, "
            "current_setting('server_version_num') AS version"
        )
        assert row is not None
        assert row["database"] == urlsplit(url).path.removeprefix("/")
        assert int(row["version"]) // 10000 == 18
    finally:
        await connection.close()


async def test_redis_accepts_ping() -> None:
    async with Redis.from_url(str(InfraTestSettings().redis_url)) as redis:
        assert await redis.ping() is True
