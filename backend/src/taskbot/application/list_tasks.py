from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.domain.tasks import Task


class ListTasks:
    def __init__(self, uows: UnitOfWorkFactory) -> None:
        self._uows = uows

    async def execute(self, user_id: int) -> tuple[Task, ...]:
        async with self._uows() as uow:
            return await uow.tasks.list_for_user(user_id)
