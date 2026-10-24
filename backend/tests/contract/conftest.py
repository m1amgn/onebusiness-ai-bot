from datetime import UTC, datetime

import pytest

from taskbot.domain.ports.persistence import UnitOfWorkFactory
from tests.fakes.persistence import FakeStore


@pytest.fixture(params=["fake"])
def uows(request: pytest.FixtureRequest) -> UnitOfWorkFactory:
    return FakeStore()


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)
