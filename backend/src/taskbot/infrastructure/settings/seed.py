from pydantic import AnyHttpUrl, field_validator

from taskbot.infrastructure.settings.base import BaseAppSettings
from taskbot.infrastructure.settings.database import DatabaseSettingsMixin
from taskbot.infrastructure.settings.extractor import ExtractorSettingsMixin
from taskbot.infrastructure.settings.redis import RedisSettingsMixin


class SeedSettings(
    BaseAppSettings, DatabaseSettingsMixin, ExtractorSettingsMixin, RedisSettingsMixin
):
    web_base_url: str = "http://localhost:5173"

    @field_validator("web_base_url")
    @classmethod
    def validate_web_url(cls, value: str) -> str:
        AnyHttpUrl(value)
        return value.rstrip("/")
