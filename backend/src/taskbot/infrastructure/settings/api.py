from taskbot.infrastructure.settings.base import BaseAppSettings
from taskbot.infrastructure.settings.database import DatabaseSettingsMixin
from taskbot.infrastructure.settings.redis import RedisSettingsMixin


class ApiSettings(BaseAppSettings, DatabaseSettingsMixin, RedisSettingsMixin):
    pass
