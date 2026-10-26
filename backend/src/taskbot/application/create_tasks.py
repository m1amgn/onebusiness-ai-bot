from dataclasses import dataclass

from taskbot.application.register_user import RegisterOrGetUser
from taskbot.domain.events import DomainEvent, TaskCreated, TaskView
from taskbot.domain.extraction import Extraction
from taskbot.domain.messages import InboundMessage, MessageKind, MessageRef, NewInboundMessage
from taskbot.domain.ports.persistence import UnitOfWork, UnitOfWorkFactory
from taskbot.domain.ports.services import Clock, TaskExtractor
from taskbot.domain.tasks import Task
from taskbot.domain.text import MessageText
from taskbot.domain.users import TelegramIdentity


@dataclass(frozen=True, slots=True)
class CreateTasksCommand:
    identity: TelegramIdentity
    ref: MessageRef
    kind: MessageKind
    text: str


@dataclass(frozen=True, slots=True)
class DuplicateMessage:
    message_id: int


@dataclass(frozen=True, slots=True)
class CreatedMessage:
    user_id: int
    message: InboundMessage
    tasks: tuple[Task, ...]


@dataclass(frozen=True, slots=True)
class TasksCreated(CreatedMessage):
    events: tuple[DomainEvent, ...]


class CreateTasksFromMessage:
    def __init__(
        self,
        uows: UnitOfWorkFactory,
        extractor: TaskExtractor,
        register: RegisterOrGetUser,
        clock: Clock,
    ) -> None:
        self._uows = uows
        self._extractor = extractor
        self._register = register
        self._clock = clock

    async def execute(self, command: CreateTasksCommand) -> TasksCreated | DuplicateMessage:
        text = MessageText.parse(command.text)
        extraction = await self._extractor.extract(text)
        async with self._uows() as uow:
            registered = await self._register.within(uow, command.identity)
            result = await self.within(
                uow,
                user_id=registered.user.id,
                ref=command.ref,
                kind=command.kind,
                text=text,
                extraction=extraction,
            )
            events = await uow.commit()
        if isinstance(result, DuplicateMessage):
            return result
        return TasksCreated(result.user_id, result.message, result.tasks, events)

    async def within(
        self,
        uow: UnitOfWork,
        *,
        user_id: int,
        ref: MessageRef,
        kind: MessageKind,
        text: MessageText,
        extraction: Extraction,
    ) -> CreatedMessage | DuplicateMessage:
        now = self._clock.now()
        message = await uow.messages.add(
            NewInboundMessage(user_id, ref, kind, text, extraction.extractor, extraction.degraded),
            now,
        )
        if message is None:
            existing = await uow.messages.get_by_ref(ref)
            if existing is None:
                raise RuntimeError("Conflicting message disappeared")
            return DuplicateMessage(existing.id)
        tasks = await uow.tasks.add_many(user_id, message.id, extraction.tasks, now)
        uow.record(*(TaskCreated(user_id=user_id, task=TaskView.from_task(task)) for task in tasks))
        return CreatedMessage(user_id, message, tasks)
