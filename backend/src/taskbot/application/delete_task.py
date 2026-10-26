from taskbot.domain.errors import TaskNotFoundError
from taskbot.domain.events import DomainEvent, TaskDeleted
from taskbot.domain.ports.persistence import UnitOfWorkFactory


class DeleteTask:
    def __init__(self, uows: UnitOfWorkFactory) -> None:
        self._uows = uows

    async def execute(self, user_id: int, task_id: int) -> tuple[DomainEvent, ...]:
        async with self._uows() as uow:
            task = await uow.tasks.get_for_update(user_id, task_id)
            if task is None:
                raise TaskNotFoundError("Task not found")
            await uow.tasks.delete(task.id)
            await uow.messages.erase_text_if_unused(task.inbound_message_id)
            uow.record(TaskDeleted(user_id=user_id, task_id=task.id))
            return await uow.commit()
