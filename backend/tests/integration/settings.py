from pydantic import PostgresDsn

from taskbot.infrastructure.settings import BaseAppSettings
from taskbot.infrastructure.settings.redis import RedisSettingsMixin


class InfraTestSettings(BaseAppSettings, RedisSettingsMixin):
    database_url: PostgresDsn = PostgresDsn(
        "postgresql+asyncpg://taskbot:taskbot@localhost:5432/taskbot"
    )
