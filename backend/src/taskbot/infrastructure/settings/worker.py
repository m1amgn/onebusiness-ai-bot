from typing import Literal

from pydantic import SecretStr

from taskbot.infrastructure.settings.base import BaseAppSettings
from taskbot.infrastructure.settings.database import DatabaseSettingsMixin
from taskbot.infrastructure.settings.redis import RedisSettingsMixin


class BrokerSettings(BaseAppSettings, RedisSettingsMixin):
    pass


class WorkerSettings(BaseAppSettings, DatabaseSettingsMixin, RedisSettingsMixin):
    worker_role: Literal["transcribe", "notify"]
    telegram_bot_token: SecretStr | None = None
