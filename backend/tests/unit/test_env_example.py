from pathlib import Path

import pytest
from dotenv import dotenv_values
from pydantic import SecretStr

from taskbot.infrastructure.settings.api import ApiSettings
from taskbot.infrastructure.settings.bot import BotSettings
from taskbot.infrastructure.settings.migration import MigrationSettings
from taskbot.infrastructure.settings.seed import SeedSettings
from taskbot.infrastructure.settings.worker import BrokerSettings, WorkerSettings
from tests.integration.settings import InfraTestSettings
from tests.settings import SampleSettings


def test_example_environment_validates_and_contains_no_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    example = Path(__file__).resolve().parents[3] / ".env.example"
    values = dotenv_values(example)
    settings_types = (
        SampleSettings,
        ApiSettings,
        InfraTestSettings,
        MigrationSettings,
        SeedSettings,
        BrokerSettings,
        WorkerSettings,
        BotSettings,
    )
    for settings_type in settings_types:
        for name, field in settings_type.model_fields.items():
            key = name.upper()
            assert key in values, f"{key} is missing from .env.example"
            if key in {
                "REDIS_URL",
                "TASK_EXTRACTOR",
                "ANTHROPIC_MODEL",
                "LOG_LEVEL",
                "WEB_BASE_URL",
                "TRANSCRIPTION_PROVIDER",
            }:
                assert values[key] == str(field.default), f"{key} differs from the settings default"
    for key in values:
        monkeypatch.delenv(key, raising=False)
        if key.endswith(("_TOKEN", "_KEY")):
            assert values[key] == "", f"{key} must be empty in the committed example"
    assert SampleSettings(_env_file=example).task_extractor == "heuristic"
    assert InfraTestSettings(_env_file=example).database_url.hosts()[0]["host"] == "localhost"
    ApiSettings(_env_file=example)
    MigrationSettings(_env_file=example)
    SeedSettings(_env_file=example)
    BrokerSettings(_env_file=example)
    WorkerSettings(_env_file=example)
    BotSettings(_env_file=example, telegram_bot_token=SecretStr("123456:synthetic"))
