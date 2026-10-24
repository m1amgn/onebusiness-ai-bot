from pydantic import PostgresDsn, RedisDsn

from taskbot.infrastructure.settings import BaseAppSettings


class InfraTestSettings(BaseAppSettings):
    database_url: PostgresDsn = PostgresDsn(
        "postgresql+asyncpg://taskbot:taskbot@localhost:5432/taskbot"
    )
    redis_url: RedisDsn = RedisDsn("redis://localhost:6379/0")
