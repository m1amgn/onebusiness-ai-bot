import pytest
from pydantic import PostgresDsn, SecretStr, ValidationError


def test_bot_settings_validate_token_provider_and_shared_web_url() -> None:
    from taskbot.infrastructure.settings.bot import BotSettings
    from taskbot.infrastructure.settings.seed import SeedSettings

    database_url = PostgresDsn("postgresql+asyncpg://localhost/test")
    settings = BotSettings(
        _env_file=None, telegram_bot_token=SecretStr("123456:synthetic"), database_url=database_url
    )
    assert str(settings.transcription_provider) == "whisper_server"
    assert settings.web_base_url == "http://localhost:5173"
    with pytest.raises(ValidationError, match="malformed token"):
        BotSettings(
            _env_file=None,
            telegram_bot_token=SecretStr("private bad token"),
            database_url=database_url,
        )
    with pytest.raises(ValidationError):
        BotSettings(
            _env_file=None,
            telegram_bot_token=SecretStr("123456:synthetic"),
            transcription_provider="invalid",  # type: ignore[arg-type]  # Invalid boundary input.
            database_url=database_url,
        )
    assert (
        SeedSettings(
            _env_file=None, web_base_url="https://example.invalid/", database_url=database_url
        ).web_base_url
        == "https://example.invalid"
    )
    with pytest.raises(ValidationError):
        SeedSettings(_env_file=None, web_base_url="not a URL", database_url=database_url)


@pytest.mark.parametrize(
    ("key", "present"), [(None, False), ("", False), ("   ", False), ("synthetic", True)]
)
def test_openai_key_presence_ignores_blank_keys(key: str | None, present: bool) -> None:
    from taskbot.infrastructure.settings.transcription import (
        TranscriptionSettingsMixin,
        has_openai_key,
    )

    settings = TranscriptionSettingsMixin(openai_api_key=None if key is None else SecretStr(key))
    assert has_openai_key(settings) is present
