from datetime import datetime

from taskbot.application.list_tasks import ListTasks
from taskbot.domain.extraction import TaskDraft
from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.domain.tasks import TaskStatus
from tests.contract.helpers import create_message


async def test_pages_keep_order_source_and_cursor_during_insert(
    uows: UnitOfWorkFactory, now: datetime
) -> None:
    created = []
    async with uows() as uow:
        for number in range(1, 8):
            mid = await create_message(uow, now, number)
            tasks = await uow.tasks.add_many(1, mid, (TaskDraft(title=str(number)),), now)
            created.append(tasks[0])
        await uow.commit()
    listing = ListTasks(uows)
    first = await listing.execute(1, status=None, after=None, limit=3)
    assert [item.task.title for item in first.items] == ["7", "6", "5"]
    assert all(item.source_text == "Call home" for item in first.items)
    assert first.next is not None
    async with uows() as uow:
        mid = await create_message(uow, now, 8)
        await uow.tasks.add_many(1, mid, (TaskDraft(title="8"),), now)
        for task in (created[1], created[5]):
            changed = task.change_status(TaskStatus.IN_PROGRESS, now)
            assert changed is not None
            await uow.tasks.save_status(changed)
        await uow.commit()
    second = await listing.execute(1, status=None, after=first.next, limit=3)
    assert [item.task.title for item in second.items] == ["4", "3", "2"]
    assert second.next is not None
    last = await listing.execute(1, status=None, after=second.next, limit=3)
    assert [item.task.title for item in last.items] == ["1"]
    assert last.next is None
    filtered = await listing.execute(1, status=TaskStatus.IN_PROGRESS, after=None, limit=3)
    assert [item.task.title for item in filtered.items] == ["6", "2"]
    assert filtered.next is None
    foreign = await listing.execute(999, status=None, after=None, limit=3)
    assert foreign.items == ()
    assert foreign.next is None


async def test_cursor_can_continue_inside_one_message(
    uows: UnitOfWorkFactory, now: datetime
) -> None:
    async with uows() as uow:
        mid = await create_message(uow, now)
        await uow.tasks.add_many(1, mid, (TaskDraft(title="a"), TaskDraft(title="b")), now)
        await uow.commit()
    listing = ListTasks(uows)
    first = await listing.execute(1, status=None, after=None, limit=1)
    assert [item.task.position for item in first.items] == [0]
    assert first.next is not None
    second = await listing.execute(1, status=None, after=first.next, limit=1)
    assert [item.task.position for item in second.items] == [1]
    assert second.next is None


async def test_full_page_retains_cursor_when_one_more_row_exists(
    uows: UnitOfWorkFactory, now: datetime
) -> None:
    async with uows() as uow:
        for number in range(1, 202):
            mid = await create_message(uow, now, number)
            await uow.tasks.add_many(1, mid, (TaskDraft(title="Task"),), now)
        await uow.commit()
    first = await ListTasks(uows).execute(1, status=None, after=None, limit=200)
    assert len(first.items) == 200
    assert first.next is not None
    last = await ListTasks(uows).execute(1, status=None, after=first.next, limit=200)
    assert len(last.items) == 1
    assert last.next is None
