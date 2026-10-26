from datetime import UTC, datetime

import pytest

from taskbot.application.dashboard_token import IssueDashboardToken
from taskbot.application.register_user import RegisterOrGetUser
from taskbot.domain.messages import MessageRef
from taskbot.domain.users import TelegramIdentity
from taskbot.infrastructure.tokens import Sha256TokenHasher
from tests.api_fixtures import api_client, fake_api
from tests.fakes.clock import FakeClock
from tests.fakes.tokens import FakeTokenGenerator


async def test_delete_erases_source_and_publishes_only_after_commit() -> None:
    fixture = await fake_api()
    source = fixture.store.state.tasks[fixture.task_id].inbound_message_id
    fixture.store.calls.items.clear()
    async with api_client(fixture, token=fixture.token) as client:
        response = await client.delete(f"/api/tasks/{fixture.task_id}")
        assert response.status_code == 204
        assert response.content == b""
        assert "content-type" not in response.headers
        assert fixture.task_id not in fixture.store.state.tasks
        assert fixture.store.state.messages[source].text is None
        assert fixture.store.state.messages[source].ref == MessageRef(7, 1)
        assert fixture.store.calls.items.index("uow.commit") < fixture.store.calls.items.index(
            "events.publish"
        )
        assert [event.model_dump() for event in fixture.publisher.events] == [
            {"type": "task.deleted", "task_id": fixture.task_id}
        ]
        assert (await client.get("/api/tasks")).json() == {"items": [], "next_cursor": None}
        again = await client.delete(f"/api/tasks/{fixture.task_id}")
    assert again.status_code == 404
    assert again.json() == {"detail": "Task not found"}
    assert len(fixture.publisher.events) == 1


async def test_failed_publication_does_not_restore_deleted_task() -> None:
    fixture = await fake_api()
    fixture.publisher.fail = True
    async with api_client(fixture, token=fixture.token) as client:
        response = await client.delete(f"/api/tasks/{fixture.task_id}")
    assert response.status_code == 204
    assert fixture.task_id not in fixture.store.state.tasks
    assert next(iter(fixture.store.state.messages.values())).text is None


async def test_another_valid_user_cannot_delete_the_task() -> None:
    fixture = await fake_api()
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    foreign = await IssueDashboardToken(
        fixture.store,
        RegisterOrGetUser(fixture.store, clock),
        FakeTokenGenerator(1),
        Sha256TokenHasher(),
        clock,
    ).execute(TelegramIdentity(8, "Another user"))
    async with api_client(fixture, token=foreign.token) as client:
        response = await client.delete(f"/api/tasks/{fixture.task_id}")
    assert response.status_code == 404
    assert response.json() == {"detail": "Task not found"}
    assert fixture.task_id in fixture.store.state.tasks
    assert next(iter(fixture.store.state.messages.values())).text == "Call home"
    assert fixture.publisher.events == []


@pytest.mark.parametrize(("task_id", "expected"), [(999, 404), (2147483648, 422), (0, 422)])
async def test_delete_refuses_missing_or_invalid_id(task_id: int, expected: int) -> None:
    fixture = await fake_api()
    async with api_client(fixture, token=fixture.token) as client:
        response = await client.delete(f"/api/tasks/{task_id}")
    assert response.status_code == expected
    assert fixture.task_id in fixture.store.state.tasks
    assert fixture.publisher.events == []
