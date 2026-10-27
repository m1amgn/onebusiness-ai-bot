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


@pytest.mark.parametrize(
    ("token", "valid"),
    [
        ("123:synthetic", True),
        ("123:a:b", True),
        ("٠١:synthetic", True),
        ("²:synthetic", True),
        ("123:zero\u200bwidth", True),
        ("123", False),
        (":synthetic", False),
        ("abc:synthetic", False),
        ("123:", False),
        ("123:has space", False),
        ("123:line\n", False),
        ("123:tab\t", False),
        ("123:nonbreaking\u00a0space", False),
    ],
)
def test_token_shape_check_matches_the_sdk_without_loading_it_at_startup(
    token: str, valid: bool
) -> None:
    from aiogram.utils.token import TokenValidationError, validate_token

    from taskbot.infrastructure.settings.telegram import validate_bot_token

    value = SecretStr(token)
    if valid:
        assert validate_token(token)
        assert validate_bot_token(value) is value
    else:
        with pytest.raises(TokenValidationError):
            validate_token(token)
        with pytest.raises(ValueError, match=r"^malformed token$"):
            validate_bot_token(value)
