from collections.abc import Callable, Sequence
from datetime import datetime
from types import TracebackType
from typing import Protocol, Self

from taskbot.domain.events import DomainEvent
from taskbot.domain.extraction import TaskDraft
from taskbot.domain.messages import InboundMessage, MessageRef, NewInboundMessage
from taskbot.domain.tasks import SourcedTask, Task, TaskCursor, TaskStatus
from taskbot.domain.users import AuthenticatedUser, TelegramIdentity, UpsertedUser, User
from taskbot.domain.voice_jobs import (
    AddedVoiceJob,
    ClaimOutcome,
    FailureKind,
    NewVoiceJob,
    NotificationOutcome,
    NotificationStart,
    TranscriptionProvider,
    UnnotifiedJob,
    VoiceJob,
)


class UserRepository(Protocol):
    async def upsert(self, identity: TelegramIdentity, now: datetime) -> UpsertedUser: ...

    async def get(self, user_id: int) -> User | None: ...

    async def find_by_token_hash(self, token_hash: str) -> AuthenticatedUser | None: ...

    async def rotate_token(self, user_id: int, token_hash: str, now: datetime) -> int | None: ...

    async def get_token_version(self, user_id: int) -> int | None: ...


class InboundMessageRepository(Protocol):
    async def add(self, new: NewInboundMessage, now: datetime) -> InboundMessage | None: ...

    async def get_by_ref(self, ref: MessageRef) -> InboundMessage | None: ...

    async def get(self, message_id: int) -> InboundMessage | None: ...

    async def erase_text_if_unused(self, message_id: int) -> None: ...


class TaskRepository(Protocol):
    async def add_many(
        self, user_id: int, inbound_message_id: int, drafts: Sequence[TaskDraft], now: datetime
    ) -> tuple[Task, ...]: ...

    async def get_for_update(self, user_id: int, task_id: int) -> Task | None: ...

    async def save_status(self, task: Task) -> Task | None: ...

    async def delete(self, task_id: int) -> None: ...

    async def page_for_user(
        self, user_id: int, *, status: TaskStatus | None, after: TaskCursor | None, fetch: int
    ) -> tuple[SourcedTask, ...]: ...

    async def list_for_message(self, inbound_message_id: int) -> tuple[Task, ...]: ...


class VoiceJobRepository(Protocol):
    async def get(self, job_id: int) -> VoiceJob | None: ...

    async def add_or_get(self, new: NewVoiceJob, now: datetime) -> AddedVoiceJob: ...

    async def set_ack_message_id(self, job_id: int, ack: int, now: datetime) -> bool: ...

    async def mark_enqueued(self, job_id: int, now: datetime) -> bool: ...

    async def claim(self, job_id: int, now: datetime) -> ClaimOutcome: ...

    async def lock_attempt(self, job_id: int, attempt: int) -> VoiceJob | None: ...

    async def requeue(self, job_id: int, attempt: int, now: datetime) -> VoiceJob | None: ...

    async def complete(
        self,
        job_id: int,
        attempt: int,
        inbound_message_id: int,
        provider: TranscriptionProvider,
        now: datetime,
    ) -> VoiceJob | None: ...

    async def fail(
        self,
        job_id: int,
        attempt: int,
        kind: FailureKind,
        provider: TranscriptionProvider,
        now: datetime,
    ) -> VoiceJob | None: ...

    async def exhaust(self, job_id: int, now: datetime) -> VoiceJob | None: ...

    async def begin_notification(self, job_id: int, now: datetime) -> NotificationStart: ...

    async def defer_notification(self, job_id: int, until: datetime, now: datetime) -> bool: ...

    async def finish_notification(
        self, job_id: int, outcome: NotificationOutcome, now: datetime
    ) -> VoiceJob | None: ...

    async def stamp_stale_processing(self, now: datetime) -> list[int]: ...

    async def stamp_stale_queued(self, now: datetime) -> list[int]: ...

    async def stamp_unnotified(self, now: datetime) -> list[UnnotifiedJob]: ...


class UnitOfWork(Protocol):
    users: UserRepository
    messages: InboundMessageRepository
    tasks: TaskRepository
    voice_jobs: VoiceJobRepository

    def record(self, *events: DomainEvent) -> None: ...

    async def commit(self) -> tuple[DomainEvent, ...]: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


type UnitOfWorkFactory = Callable[[], UnitOfWork]
