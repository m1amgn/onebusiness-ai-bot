import time
from dataclasses import dataclass, field

import pytest

from taskbot.entrypoints.api import container
from taskbot.infrastructure.settings.api import ApiSettings


@dataclass
class OwnedResources:
    closed: list[str] = field(default_factory=list)

    async def aclose(self) -> None:
        self.closed.append("redis")

    async def dispose(self) -> None:
        self.closed.append("engine")


async def test_api_services_use_monotonic_time_for_stream_deadlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TASKBOT_ENV_FILE", "/dev/null")
    settings = ApiSettings.model_validate(
        {
            "database_url": "postgresql+asyncpg://unused/test",
            "redis_url": "redis://127.0.0.1:1/0",
        }
    )
    owned = OwnedResources()
    monkeypatch.setattr(container, "create_engine_and_sessions", lambda url: (owned, object()))
    monkeypatch.setattr(container, "create_redis_client", lambda url, **kwargs: owned)

    async with container.open_api_services(settings) as services:
        assert services.stream_events._monotonic is time.monotonic
    assert owned.closed == ["redis", "engine"]
