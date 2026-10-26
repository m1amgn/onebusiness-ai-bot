from dataclasses import dataclass

from taskbot.domain.errors import TaskNotFoundError
from taskbot.domain.events import DomainEvent, TaskUpdated, TaskView
from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.domain.ports.services import Clock
from taskbot.domain.tasks import Task, TaskStatus


@dataclass(frozen=True, slots=True)
class TaskChange:
    task: Task
    view: TaskView
    changed: bool
    events: tuple[DomainEvent, ...]


class ChangeTaskStatus:
    def __init__(self, uows: UnitOfWorkFactory, clock: Clock) -> None:
        self._uows = uows
        self._clock = clock

    async def execute(self, user_id: int, task_id: int, status: TaskStatus) -> TaskChange:
        async with self._uows() as uow:
            task = await uow.tasks.get_for_update(user_id, task_id)
            if task is None:
                raise TaskNotFoundError("Task not found")
            message = await uow.messages.get(task.inbound_message_id)
            if message is None or message.text is None:
                raise RuntimeError("Task has no source message text")
            changed = task.change_status(status, self._clock.now())
            if changed is not None:
                saved = await uow.tasks.save_status(changed)
                if saved is None:
                    raise RuntimeError("Task disappeared while locked")
                task = saved
                uow.record(
                    TaskUpdated(user_id=user_id, task=TaskView.from_task(task, message.text))
                )
            events = await uow.commit()
        return TaskChange(task, TaskView.from_task(task, message.text), changed is not None, events)
