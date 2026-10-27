from taskbot.infrastructure.settings.base import BaseAppSettings
from taskbot.infrastructure.settings.database import DatabaseSettingsMixin
from taskbot.infrastructure.settings.extractor import ExtractorSettingsMixin
from taskbot.infrastructure.settings.redis import RedisSettingsMixin
from taskbot.infrastructure.settings.web import WebSettingsMixin


class SeedSettings(
    BaseAppSettings,
    DatabaseSettingsMixin,
    ExtractorSettingsMixin,
    RedisSettingsMixin,
    WebSettingsMixin,
):
    pass
