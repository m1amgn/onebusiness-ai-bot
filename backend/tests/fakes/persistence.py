"""Transactional behavior fake, not a database constraint simulator.

PostgreSQL alone enforces foreign keys, general unique constraints and sequence gaps.
The fake models the repository-specific duplicate rules and reuses ids after rollback.
Real-store integration tests verify the storage constraints and concurrency windows.
"""

import asyncio
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from types import TracebackType
from typing import Self

from taskbot.domain.events import DomainEvent
from taskbot.domain.extraction import TaskDraft
from taskbot.domain.limits import SWEEP_STALE_SECONDS, SWEEP_UNNOTIFIED_SECONDS
from taskbot.domain.messages import InboundMessage, MessageRef, NewInboundMessage
from taskbot.domain.ports.persistence import (
    InboundMessageRepository,
    TaskRepository,
    UserRepository,
    VoiceJobRepository,
)
from taskbot.domain.tasks import Task, TaskStatus
from taskbot.domain.time import require_aware
from taskbot.domain.users import AuthenticatedUser, TelegramIdentity, UpsertedUser, User
from taskbot.domain.voice_jobs import (
    AddedVoiceJob,
    Claimed,
    ClaimOutcome,
    FailureKind,
    NewVoiceJob,
    NotificationOutcome,
    NotificationStart,
    NotificationStarted,
    TranscriptionProvider,
    UnnotifiedJob,
    VoiceJob,
    VoiceJobStatus,
    classify_claim_refusal,
    classify_notification_refusal,
)
from tests.fakes.calls import Calls


@dataclass
class State:
    users: dict[int, User] = field(default_factory=dict)
    hashes: dict[int, str] = field(default_factory=dict)
    messages: dict[int, InboundMessage] = field(default_factory=dict)
    tasks: dict[int, Task] = field(default_factory=dict)
    voice_jobs: dict[int, VoiceJob] = field(default_factory=dict)


def next_id(rows: Mapping[int, object]) -> int:
    return max(rows, default=0) + 1


class FakeRepository:
    def __init__(self, state: State, store: FakeStore) -> None:
        self.state = state
        self.store = store

    def called(self, operation: str) -> None:
        self.store.calls.items.append(operation)
        if self.store.fail_on == operation:
            raise RuntimeError(f"forced failure: {operation}")


class FakeUserRepository(FakeRepository):
    async def upsert(self, identity: TelegramIdentity, now: datetime) -> UpsertedUser:
        require_aware(now)
        self.called("users.upsert")
        existing = next(
            (
                u
                for u in self.state.users.values()
                if u.telegram_user_id == identity.telegram_user_id
            ),
            None,
        )
        if existing is None:
            user = User(
                next_id(self.state.users),
                identity.telegram_user_id,
                identity.display_name,
                0,
                now,
                now,
            )
        else:
            user = replace(existing, display_name=identity.display_name, updated_at=now)
        self.state.users[user.id] = user
        return UpsertedUser(user, existing is None)

    async def get(self, user_id: int) -> User | None:
        self.called("users.get")
        return self.state.users.get(user_id)

    async def find_by_token_hash(self, token_hash: str) -> AuthenticatedUser | None:
        self.called("users.find_by_token_hash")
        user_id = next((i for i, h in self.state.hashes.items() if h == token_hash), None)
        if user_id is None:
            return None
        return AuthenticatedUser(user_id, self.state.users[user_id].token_version)

    async def rotate_token(self, user_id: int, token_hash: str, now: datetime) -> int | None:
        require_aware(now)
        self.called("users.rotate_token")
        user = self.state.users.get(user_id)
        if user is None:
            return None
        user = replace(user, token_version=user.token_version + 1, updated_at=now)
        self.state.users[user_id] = user
        self.state.hashes[user_id] = token_hash
        return user.token_version

    async def get_token_version(self, user_id: int) -> int | None:
        self.called("users.get_token_version")
        user = self.state.users.get(user_id)
        return None if user is None else user.token_version


class FakeMessageRepository(FakeRepository):
    async def add(self, new: NewInboundMessage, now: datetime) -> InboundMessage | None:
        require_aware(now)
        self.called("messages.add")
        if any(m.ref == new.ref for m in self.state.messages.values()):
            return None
        message = InboundMessage(
            next_id(self.state.messages),
            new.user_id,
            new.ref,
            new.kind,
            new.text.value,
            new.extractor,
            new.degraded,
            now,
        )
        self.state.messages[message.id] = message
        return message

    async def get_by_ref(self, ref: MessageRef) -> InboundMessage | None:
        self.called("messages.get_by_ref")
        return next((m for m in self.state.messages.values() if m.ref == ref), None)

    async def get(self, message_id: int) -> InboundMessage | None:
        self.called("messages.get")
        return self.state.messages.get(message_id)


class FakeTaskRepository(FakeRepository):
    async def add_many(
        self, user_id: int, inbound_message_id: int, drafts: Sequence[TaskDraft], now: datetime
    ) -> tuple[Task, ...]:
        require_aware(now)
        self.called("tasks.add_many")
        tasks = []
        for position, draft in enumerate(drafts):
            task = Task(
                next_id(self.state.tasks),
                user_id,
                inbound_message_id,
                position,
                draft.title,
                draft.description,
                TaskStatus.PENDING,
                now,
                now,
            )
            self.state.tasks[task.id] = task
            tasks.append(task)
        return tuple(tasks)

    async def get_for_update(self, user_id: int, task_id: int) -> Task | None:
        self.called("tasks.get_for_update")
        task = self.state.tasks.get(task_id)
        return task if task is not None and task.user_id == user_id else None

    async def save_status(self, task: Task) -> Task | None:
        require_aware(task.updated_at)
        self.called("tasks.save_status")
        existing = self.state.tasks.get(task.id)
        if existing is None or existing.user_id != task.user_id:
            return None
        saved = replace(existing, status=task.status, updated_at=task.updated_at)
        self.state.tasks[task.id] = saved
        return saved

    async def list_for_user(self, user_id: int) -> tuple[Task, ...]:
        self.called("tasks.list_for_user")
        return tuple(
            sorted(
                (t for t in self.state.tasks.values() if t.user_id == user_id),
                key=lambda t: (-t.inbound_message_id, t.position),
            )
        )

    async def list_for_message(self, inbound_message_id: int) -> tuple[Task, ...]:
        self.called("tasks.list_for_message")
        return tuple(
            sorted(
                (
                    t
                    for t in self.state.tasks.values()
                    if t.inbound_message_id == inbound_message_id
                ),
                key=lambda t: t.position,
            )
        )


class FakeVoiceJobRepository(FakeRepository):
    def apply(
        self,
        job_id: int,
        operation: str,
        transition: Callable[[VoiceJob], VoiceJob | None],
        *,
        now: datetime,
    ) -> VoiceJob | None:
        require_aware(now)
        self.called(operation)
        job = self.state.voice_jobs.get(job_id)
        changed = None if job is None else transition(job)
        if changed is not None:
            self.state.voice_jobs[job_id] = changed
        return changed

    async def get(self, job_id: int) -> VoiceJob | None:
        self.called("voice_jobs.get")
        return self.state.voice_jobs.get(job_id)

    async def add_or_get(self, new: NewVoiceJob, now: datetime) -> AddedVoiceJob:
        require_aware(now)
        self.called("voice_jobs.add_or_get")
        existing = next((j for j in self.state.voice_jobs.values() if j.ref == new.ref), None)
        if existing is not None:
            return AddedVoiceJob(existing, False)
        job = VoiceJob(
            next_id(self.state.voice_jobs),
            new.user_id,
            new.ref,
            new.file_id,
            new.file_unique_id,
            new.duration_seconds,
            new.file_size,
            now,
            now,
            now,
        )
        self.state.voice_jobs[job.id] = job
        return AddedVoiceJob(job, True)

    async def set_ack_message_id(self, job_id: int, ack: int, now: datetime) -> bool:
        return (
            self.apply(
                job_id, "voice_jobs.set_ack", lambda j: j.set_ack_message_id(ack, now), now=now
            )
            is not None
        )

    async def mark_enqueued(self, job_id: int, now: datetime) -> bool:
        return (
            self.apply(job_id, "voice_jobs.mark_enqueued", lambda j: j.mark_enqueued(now), now=now)
            is not None
        )

    async def claim(self, job_id: int, now: datetime) -> ClaimOutcome:
        require_aware(now)
        job = self.apply(job_id, "voice_jobs.claim", lambda j: j.claim(now), now=now)
        return (
            Claimed(job)
            if job is not None
            else classify_claim_refusal(self.state.voice_jobs.get(job_id), now)
        )

    async def lock_attempt(self, job_id: int, attempt: int) -> VoiceJob | None:
        self.called("voice_jobs.lock_attempt")
        job = self.state.voice_jobs.get(job_id)
        return None if job is None else job.lock_attempt(attempt)

    async def requeue(self, job_id: int, attempt: int, now: datetime) -> VoiceJob | None:
        return self.apply(job_id, "voice_jobs.requeue", lambda j: j.requeue(attempt, now), now=now)

    async def complete(
        self,
        job_id: int,
        attempt: int,
        inbound_message_id: int,
        provider: TranscriptionProvider,
        now: datetime,
    ) -> VoiceJob | None:
        return self.apply(
            job_id,
            "voice_jobs.complete",
            lambda j: j.complete(attempt, inbound_message_id, provider, now),
            now=now,
        )

    async def fail(
        self,
        job_id: int,
        attempt: int,
        kind: FailureKind,
        provider: TranscriptionProvider,
        now: datetime,
    ) -> VoiceJob | None:
        return self.apply(
            job_id, "voice_jobs.fail", lambda j: j.fail(attempt, kind, provider, now), now=now
        )

    async def exhaust(self, job_id: int, now: datetime) -> VoiceJob | None:
        return self.apply(job_id, "voice_jobs.exhaust", lambda j: j.exhaust(now), now=now)

    async def begin_notification(self, job_id: int, now: datetime) -> NotificationStart:
        require_aware(now)
        job = self.apply(
            job_id, "voice_jobs.begin_notification", lambda j: j.begin_notification(now), now=now
        )
        return (
            NotificationStarted(job)
            if job is not None
            else classify_notification_refusal(self.state.voice_jobs.get(job_id), now)
        )

    async def defer_notification(self, job_id: int, until: datetime, now: datetime) -> bool:
        require_aware(until)
        return (
            self.apply(
                job_id,
                "voice_jobs.defer_notification",
                lambda j: j.defer_notification(until, now),
                now=now,
            )
            is not None
        )

    async def finish_notification(
        self, job_id: int, outcome: NotificationOutcome, now: datetime
    ) -> VoiceJob | None:
        return self.apply(
            job_id,
            "voice_jobs.finish_notification",
            lambda j: j.finish_notification(outcome, now),
            now=now,
        )

    def stamp(self, now: datetime, predicate: Callable[[VoiceJob], bool]) -> list[VoiceJob]:
        require_aware(now)
        selected = [j for j in self.state.voice_jobs.values() if predicate(j)][:100]
        for job in selected:
            changed = job.mark_enqueued(now)
            assert changed is not None
            self.state.voice_jobs[job.id] = changed
        return selected

    async def stamp_stale_processing(self, now: datetime) -> list[int]:
        cutoff = now - timedelta(seconds=SWEEP_STALE_SECONDS)
        return [
            j.id
            for j in self.stamp(
                now,
                lambda j: (
                    j.status == VoiceJobStatus.PROCESSING
                    and j.claimed_at is not None
                    and (j.claimed_at < cutoff)
                    and (j.last_enqueued_at < cutoff)
                ),
            )
        ]

    async def stamp_stale_queued(self, now: datetime) -> list[int]:
        cutoff = now - timedelta(seconds=SWEEP_STALE_SECONDS)
        return [
            j.id
            for j in self.stamp(
                now, lambda j: j.status == VoiceJobStatus.QUEUED and j.last_enqueued_at < cutoff
            )
        ]

    async def stamp_unnotified(self, now: datetime) -> list[UnnotifiedJob]:
        cutoff = now - timedelta(seconds=SWEEP_UNNOTIFIED_SECONDS)
        return [
            UnnotifiedJob(j.id, j.status)
            for j in self.stamp(
                now,
                lambda j: (
                    j.terminal
                    and j.notified_at is None
                    and (j.last_enqueued_at < cutoff)
                    and (j.notify_not_before is None or j.notify_not_before <= now)
                ),
            )
        ]


@dataclass
class FakeStore:
    state: State = field(default_factory=State)
    calls: Calls = field(default_factory=Calls)
    fail_on: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def __call__(self) -> FakeUnitOfWork:
        return FakeUnitOfWork(self)


class FakeUnitOfWork:
    users: UserRepository
    messages: InboundMessageRepository
    tasks: TaskRepository
    voice_jobs: VoiceJobRepository

    def __init__(self, store: FakeStore) -> None:
        self.store = store
        self.events: list[DomainEvent] = []

    async def __aenter__(self) -> Self:
        await self.store.lock.acquire()
        self.store.calls.items.append("uow.open")
        self.working = deepcopy(self.store.state)
        self.users = FakeUserRepository(self.working, self.store)
        self.messages = FakeMessageRepository(self.working, self.store)
        self.tasks = FakeTaskRepository(self.working, self.store)
        self.voice_jobs = FakeVoiceJobRepository(self.working, self.store)
        return self

    def record(self, *events: DomainEvent) -> None:
        self.events.extend(events)

    async def commit(self) -> tuple[DomainEvent, ...]:
        if self.store.fail_on == "commit":
            raise RuntimeError("forced failure: commit")
        self.store.state = deepcopy(self.working)
        self.store.calls.items.append("uow.commit")
        events = tuple(self.events)
        self.events.clear()
        return events

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.events.clear()
        self.store.calls.items.append("uow.close")
        self.store.lock.release()
