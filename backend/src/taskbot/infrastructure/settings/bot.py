from aiogram.utils.token import TokenValidationError, validate_token
from pydantic import SecretStr, field_validator

from taskbot.infrastructure.settings.base import BaseAppSettings
from taskbot.infrastructure.settings.database import DatabaseSettingsMixin
from taskbot.infrastructure.settings.extractor import ExtractorSettingsMixin
from taskbot.infrastructure.settings.redis import RedisSettingsMixin
from taskbot.infrastructure.settings.transcription import TranscriptionSettingsMixin
from taskbot.infrastructure.settings.web import WebSettingsMixin


class BotSettings(
    BaseAppSettings,
    DatabaseSettingsMixin,
    RedisSettingsMixin,
    ExtractorSettingsMixin,
    WebSettingsMixin,
    TranscriptionSettingsMixin,
):
    telegram_bot_token: SecretStr

    @field_validator("telegram_bot_token")
    @classmethod
    def validate_bot_token(cls, value: SecretStr) -> SecretStr:
        try:
            validate_token(value.get_secret_value())
        except TokenValidationError:
            raise ValueError("malformed token") from None
        return value
