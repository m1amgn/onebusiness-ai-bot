from dataclasses import replace

import pytest

from taskbot.application.change_task_status import ChangeTaskStatus
from taskbot.application.delete_task import DeleteTask
from taskbot.application.list_tasks import ListTasks
from taskbot.domain.errors import TaskNotFoundError
from taskbot.domain.tasks import TaskStatus
from tests.api_fixtures import fake_api
from tests.fakes.clock import FakeClock


@pytest.mark.parametrize("owner", [False, True])
async def test_missing_or_foreign_task_emits_nothing(owner: bool) -> None:
    fixture = await fake_api()
    fixture.store.calls.items.clear()
    with pytest.raises(TaskNotFoundError):
        await DeleteTask(fixture.store).execute(
            fixture.user_id if owner else fixture.user_id + 1,
            fixture.task_id + 1 if owner else fixture.task_id,
        )
    assert fixture.task_id in fixture.store.state.tasks
    assert next(iter(fixture.store.state.messages.values())).text == "Call home"
    assert "uow.commit" not in fixture.store.calls.items


@pytest.mark.parametrize("failure", ["tasks.delete", "messages.erase_text_if_unused", "commit"])
async def test_delete_and_text_erasure_roll_back_together(failure: str) -> None:
    fixture = await fake_api()
    before = fixture.store.state
    fixture.store.fail_on = failure
    with pytest.raises(RuntimeError, match="forced failure"):
        await DeleteTask(fixture.store).execute(fixture.user_id, fixture.task_id)
    assert fixture.store.state == before
    assert not fixture.store.lock.locked()
    fixture.store.fail_on = None
    events = await DeleteTask(fixture.store).execute(fixture.user_id, fixture.task_id)
    assert len(events) == 1
    assert events[0].type == "task.deleted"
    assert events[0].task_id == fixture.task_id


@pytest.mark.parametrize("missing", [False, True], ids=["erased", "missing"])
async def test_status_refuses_a_task_without_source_text(missing: bool) -> None:
    fixture = await fake_api()
    source = next(iter(fixture.store.state.messages.values()))
    if missing:
        del fixture.store.state.messages[source.id]
    else:
        fixture.store.state.messages[source.id] = replace(source, text=None)
    with pytest.raises(RuntimeError, match="Task has no source message text"):
        await ChangeTaskStatus(fixture.store, FakeClock(source.created_at)).execute(
            fixture.user_id, fixture.task_id, TaskStatus.COMPLETED
        )
    assert fixture.store.state.tasks[fixture.task_id].status == TaskStatus.PENDING
    assert not fixture.store.lock.locked()


async def test_listing_refuses_erased_text_while_a_task_exists() -> None:
    fixture = await fake_api()
    source = next(iter(fixture.store.state.messages.values()))
    fixture.store.state.messages[source.id] = replace(source, text=None)
    with pytest.raises(RuntimeError, match="Task has no source message text"):
        await ListTasks(fixture.store).execute(fixture.user_id, status=None, after=None, limit=50)
