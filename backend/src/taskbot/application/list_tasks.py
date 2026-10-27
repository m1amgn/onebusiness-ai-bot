from dataclasses import dataclass

from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.domain.tasks import Task, TaskCursor, TaskListing, TaskStatus

CURRENT_TASKS_LIMIT = 15


class ListTasks:
    def __init__(self, uows: UnitOfWorkFactory) -> None:
        self._uows = uows

    async def execute(
        self, user_id: int, *, status: TaskStatus | None, after: TaskCursor | None, limit: int
    ) -> TaskListing:
        if limit < 1:
            raise ValueError("Task page limit must be positive")
        async with self._uows() as uow:
            fetched = await uow.tasks.page_for_user(
                user_id, status=status, after=after, fetch=limit + 1
            )
        items = fetched[:limit]
        last = items[-1].task if len(fetched) > limit else None
        cursor = None if last is None else TaskCursor(last.inbound_message_id, last.position)
        return TaskListing(items, cursor)


@dataclass(frozen=True, slots=True)
class CurrentTasks:
    in_progress: tuple[Task, ...]
    pending: tuple[Task, ...]
    more: bool


class ListCurrentTasks:
    def __init__(self, list_tasks: ListTasks) -> None:
        self._list_tasks = list_tasks

    async def execute(self, user_id: int) -> CurrentTasks:
        in_progress = await self._list_tasks.execute(
            user_id, status=TaskStatus.IN_PROGRESS, after=None, limit=CURRENT_TASKS_LIMIT
        )
        room = CURRENT_TASKS_LIMIT - len(in_progress.items)
        pending = await self._list_tasks.execute(
            user_id, status=TaskStatus.PENDING, after=None, limit=max(room, 1)
        )
        shown_pending = pending.items if room > 0 else ()
        return CurrentTasks(
            in_progress=tuple(item.task for item in in_progress.items),
            pending=tuple(item.task for item in shown_pending),
            more=(
                in_progress.next is not None
                or pending.next is not None
                or (room == 0 and bool(pending.items))
            ),
        )
