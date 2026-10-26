import os
from pathlib import Path
from typing import Literal

from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class BaseAppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[5] / ".env",
        extra="ignore",
        env_ignore_empty=True,
    )

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        override = os.environ.get("TASKBOT_ENV_FILE")
        if (
            override is not None
            and isinstance(dotenv_settings, DotEnvSettingsSource)
            and dotenv_settings.env_file == cls.model_config["env_file"]
        ):
            dotenv_settings = DotEnvSettingsSource(settings_cls, env_file=override)
        return init_settings, env_settings, dotenv_settings, file_secret_settings
