from datetime import UTC, datetime

from taskbot.domain.extraction import TaskDraft
from taskbot.domain.tasks import TaskStatus
from tests.api_fixtures import api_client, fake_api
from tests.contract.helpers import create_message


async def test_route_pages_round_trip_cursor_and_filter_status() -> None:
    fixture = await fake_api()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    async with fixture.store() as uow:
        for number, title in [(2, "Second task"), (3, "Third task")]:
            mid = await create_message(uow, now, number)
            (task,) = await uow.tasks.add_many(fixture.user_id, mid, (TaskDraft(title=title),), now)
            if number == 3:
                changed = task.change_status(TaskStatus.IN_PROGRESS, now)
                assert changed is not None
                await uow.tasks.save_status(changed)
        await uow.commit()
    async with api_client(fixture, token=fixture.token) as client:
        params: dict[str, str | int] = {"limit": 1}
        titles = []
        for index in range(3):
            response = await client.get("/api/tasks", params=params)
            assert response.status_code == 200
            page = response.json()
            assert len(page["items"]) == 1
            titles.append(page["items"][0]["title"])
            cursor = page["next_cursor"]
            if index < 2:
                assert isinstance(cursor, str)
                assert cursor
                params["cursor"] = cursor
            else:
                assert cursor is None
        filtered = await client.get("/api/tasks", params={"status": "in_progress", "limit": 1})
    assert titles == ["Third task", "Second task", "Call home"]
    assert filtered.status_code == 200
    assert [item["title"] for item in filtered.json()["items"]] == ["Third task"]
    assert filtered.json()["next_cursor"] is None
