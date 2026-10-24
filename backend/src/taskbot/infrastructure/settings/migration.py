from taskbot.infrastructure.settings.base import BaseAppSettings
from taskbot.infrastructure.settings.database import DatabaseSettingsMixin


class MigrationSettings(BaseAppSettings, DatabaseSettingsMixin):
    pass
