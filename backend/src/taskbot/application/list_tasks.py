from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.domain.tasks import TaskCursor, TaskListing, TaskStatus


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
