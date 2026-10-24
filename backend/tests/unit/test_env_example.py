from pathlib import Path

import pytest
from dotenv import dotenv_values

from taskbot.infrastructure.settings.migration import MigrationSettings
from taskbot.infrastructure.settings.seed import SeedSettings
from tests.integration.settings import InfraTestSettings
from tests.settings import SampleSettings


def test_example_environment_validates_and_contains_no_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    example = Path(__file__).resolve().parents[3] / ".env.example"
    values = dotenv_values(example)
    settings_types = (SampleSettings, InfraTestSettings, MigrationSettings, SeedSettings)
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
            }:
                assert values[key] == str(field.default), f"{key} differs from the settings default"
    for key in values:
        monkeypatch.delenv(key, raising=False)
        if key.endswith(("_TOKEN", "_KEY")):
            assert values[key] == "", f"{key} must be empty in the committed example"
    assert SampleSettings(_env_file=example).task_extractor == "heuristic"
    assert InfraTestSettings(_env_file=example).database_url.hosts()[0]["host"] == "localhost"
    MigrationSettings(_env_file=example)
    SeedSettings(_env_file=example)
