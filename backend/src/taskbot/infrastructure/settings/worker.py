from typing import Literal

from pydantic import SecretStr, field_validator

from taskbot.infrastructure.settings.base import BaseAppSettings
from taskbot.infrastructure.settings.database import DatabaseSettingsMixin
from taskbot.infrastructure.settings.extractor import ExtractorSettingsMixin
from taskbot.infrastructure.settings.redis import RedisSettingsMixin
from taskbot.infrastructure.settings.telegram import validate_bot_token
from taskbot.infrastructure.settings.transcription import TranscriberSettingsMixin


class BrokerSettings(BaseAppSettings, RedisSettingsMixin):
    pass


class WorkerSettings(BaseAppSettings, DatabaseSettingsMixin, RedisSettingsMixin):
    worker_role: Literal["transcribe", "notify"]
    telegram_bot_token: SecretStr | None = None

    @field_validator("telegram_bot_token")
    @classmethod
    def validate_telegram_token(cls, value: SecretStr | None) -> SecretStr | None:
        return None if value is None else validate_bot_token(value)


class TranscribeWorkerSettings(WorkerSettings, TranscriberSettingsMixin, ExtractorSettingsMixin):
    pass
