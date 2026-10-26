from datetime import UTC, datetime
from typing import cast

import pytest

from taskbot.domain.ports.persistence import UnitOfWorkFactory
from tests.fakes.persistence import FakeStore


@pytest.fixture(params=["fake", pytest.param("postgres", marks=pytest.mark.integration)])
def uows(request: pytest.FixtureRequest) -> UnitOfWorkFactory:
    if request.param == "fake":
        return FakeStore()
    # pytest's dynamic fixture lookup erases the declared fixture return type.
    return cast(UnitOfWorkFactory, request.getfixturevalue("postgres_uows"))


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)
