from pathlib import Path

import pytest
from pydantic import ValidationError

from taskbot.infrastructure.settings import BaseAppSettings, ExtractorSettingsMixin


class SampleSettings(BaseAppSettings, ExtractorSettingsMixin):
    pass


@pytest.fixture(autouse=True)
def clear_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("TASK_EXTRACTOR", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "LOG_LEVEL"):
        monkeypatch.delenv(key, raising=False)


def test_mixin_reads_file_values(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "TASK_EXTRACTOR=claude\nANTHROPIC_API_KEY=synthetic-test-key\n"
        "ANTHROPIC_MODEL=test-model\nLOG_LEVEL=DEBUG\nUNRELATED_VARIABLE=ignored\n"
    )
    settings = SampleSettings(_env_file=dotenv)
    assert settings.task_extractor == "claude"
    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "synthetic-test-key"
    assert "synthetic-test-key" not in repr(settings)
    assert settings.anthropic_model == "test-model"
    assert settings.log_level == "DEBUG"


def test_empty_key_degrades_to_none(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("ANTHROPIC_API_KEY=\n")
    assert SampleSettings(_env_file=dotenv).anthropic_api_key is None


def test_invalid_extractor_is_rejected(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("TASK_EXTRACTOR=gpt\n")
    with pytest.raises(ValidationError, match="task_extractor"):
        SampleSettings(_env_file=dotenv)


def test_shell_overrides_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("TASK_EXTRACTOR=claude\n")
    monkeypatch.setenv("TASK_EXTRACTOR", "heuristic")
    assert SampleSettings(_env_file=dotenv).task_extractor == "heuristic"


def test_invalid_log_level_is_rejected(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("LOG_LEVEL=LOUD\n")
    with pytest.raises(ValidationError, match="log_level"):
        SampleSettings(_env_file=dotenv)
