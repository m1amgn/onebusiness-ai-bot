from datetime import UTC, datetime

import pytest
from pydantic import ValidationError


def test_database_settings_require_the_async_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    from taskbot.infrastructure.settings.migration import MigrationSettings
    from taskbot.infrastructure.settings.seed import SeedSettings

    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError, match="database_url"):
        MigrationSettings(_env_file=None)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")
    with pytest.raises(ValidationError, match="database_url"):
        MigrationSettings(_env_file=None)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    assert MigrationSettings(_env_file=None).database_url.scheme == "postgresql+asyncpg"
    monkeypatch.setenv("WEB_BASE_URL", "not a URL")
    with pytest.raises(ValidationError, match="web_base_url"):
        SeedSettings(_env_file=None)


def test_database_url_helpers_and_destructive_guard() -> None:
    from tests.databases import maintenance_url, make_alembic_config, quote_identifier, raw_url

    original = "postgresql+asyncpg://u:p%40ss@localhost:5432/taskbot"
    assert maintenance_url(original) == "postgresql://u:p%40ss@localhost:5432/postgres"
    assert raw_url(original).endswith("/taskbot")
    assert quote_identifier('a"b') == '"a""b"'
    with pytest.raises(ValueError, match="test database"):
        make_alembic_config(original)
    url = original.replace("/taskbot", "/taskbot_test_1792800000_abcdef01")
    assert make_alembic_config(url).attributes["database_url"] == url


@pytest.mark.parametrize(
    "name", ["taskbot", "taskbot_test_x", "taskbot_test_1_abcdef01\n", "taskbot_test_١_abcdef01"]
)
def test_cleanup_rejects_non_test_names(name: str) -> None:
    from tests.databases import is_abandoned

    assert not is_abandoned(name, datetime(2026, 10, 24, tzinfo=UTC))
