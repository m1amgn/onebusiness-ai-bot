from pathlib import Path

import pytest
from dotenv import dotenv_values

from taskbot.infrastructure.settings import BaseAppSettings, ExtractorSettingsMixin
from tests.integration.settings import InfraTestSettings


class ExampleSettings(BaseAppSettings, ExtractorSettingsMixin):
    pass


def test_example_environment_validates_and_contains_no_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    example = Path(__file__).resolve().parents[3] / ".env.example"
    values = dotenv_values(example)
    for key in values:
        monkeypatch.delenv(key, raising=False)
        if key.endswith(("_TOKEN", "_KEY")):
            assert values[key] == "", f"{key} must be empty in the committed example"
    assert ExampleSettings(_env_file=example).task_extractor == "heuristic"
    assert InfraTestSettings(_env_file=example).database_url.hosts()[0]["host"] == "localhost"
