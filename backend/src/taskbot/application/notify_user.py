import logging
import math
from dataclasses import dataclass
from datetime import timedelta

from taskbot.application.replies import notification_text
from taskbot.domain.errors import MessageGoneError, RecipientBlockedError, RetryAfterError
from taskbot.domain.jobs import NotificationJob
from taskbot.domain.limits import MAX_NOTIFY_ATTEMPTS, retry_delay_seconds
from taskbot.domain.messages import InboundMessage
from taskbot.domain.ports.messaging import JobQueue
from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.domain.ports.services import Clock
from taskbot.domain.ports.telegram import UserNotifier
from taskbot.domain.tasks import Task
from taskbot.domain.voice_jobs import (
    NotificationCeilingReached,
    NotificationNotDue,
    NotificationOutcome,
    NotificationStart,
    NotificationStarted,
    VoiceJob,
    VoiceJobStatus,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Finished:
    outcome: NotificationOutcome


@dataclass(frozen=True, slots=True)
class Deferred:
    delay_seconds: int


@dataclass(frozen=True, slots=True)
class Retrying:
    delay_seconds: int


@dataclass(frozen=True, slots=True)
class NotDue:
    delay_seconds: int


@dataclass(frozen=True, slots=True)
class Dropped:
    pass


type NotifyResult = Finished | Deferred | Retrying | NotDue | Dropped


@dataclass(frozen=True, slots=True)
class _Ready:
    row: VoiceJob
    message: InboundMessage | None
    task: Task | None


class NotifyUser:
    def __init__(
        self, uows: UnitOfWorkFactory, notifier: UserNotifier, queue: JobQueue, clock: Clock
    ) -> None:
        self._uows = uows
        self._notifier = notifier
        self._queue = queue
        self._clock = clock

    async def execute(self, job: NotificationJob) -> NotifyResult:
        ready = await self._begin(job.voice_job_id)
        if isinstance(ready, NotificationNotDue):
            delay = max(ready.remaining_seconds, 1)
            await self._enqueue(job, delay)
            return NotDue(delay)
        if isinstance(ready, NotificationCeilingReached):
            logger.warning("Voice notification ceiling reached: %s", job.voice_job_id)
            return Finished(NotificationOutcome.GIVEN_UP)
        if not isinstance(ready, _Ready):
            return Dropped()
        row = ready.row
        if row.status != job.status:
            logger.info("Voice notification status mismatch: %s", row.id)
        text = notification_text(row, ready.message, ready.task)
        try:
            outcome = await self._send(row, text)
        except RecipientBlockedError:
            outcome = NotificationOutcome.BLOCKED
        except RetryAfterError as error:
            return await self._defer(row, error.seconds)
        except Exception as error:
            return await self._retry(row, error)
        return await self._finish(row.id, outcome)

    async def _begin(self, job_id: int) -> _Ready | NotificationStart:
        async with self._uows() as uow:
            started = await uow.voice_jobs.begin_notification(job_id, self._clock.now())
            if isinstance(started, NotificationCeilingReached):
                await uow.voice_jobs.finish_notification(
                    job_id, NotificationOutcome.GIVEN_UP, self._clock.now()
                )
            if not isinstance(started, NotificationStarted):
                await uow.commit()
                return started
            row = started.job
            message = None
            task = None
            if row.status == VoiceJobStatus.DONE and row.inbound_message_id is not None:
                message = await uow.messages.get(row.inbound_message_id)
                tasks = await uow.tasks.list_for_message(row.inbound_message_id)
                task = tasks[0] if tasks else None
            await uow.commit()
        return _Ready(row, message, task)

    async def _send(self, row: VoiceJob, text: str) -> NotificationOutcome:
        if row.ack_message_id is not None:
            try:
                await self._notifier.edit_message(row.ref.chat_id, row.ack_message_id, text)
                return NotificationOutcome.EDITED
            except MessageGoneError:
                pass
        await self._notifier.send_reply(
            row.ref.chat_id, text, reply_to_message_id=row.ref.message_id
        )
        return NotificationOutcome.REPLIED

    async def _finish(self, job_id: int, outcome: NotificationOutcome) -> NotifyResult:
        async with self._uows() as uow:
            row = await uow.voice_jobs.finish_notification(job_id, outcome, self._clock.now())
            await uow.commit()
        return Dropped() if row is None else Finished(row.notification_outcome or outcome)

    async def _defer(self, row: VoiceJob, seconds: int) -> NotifyResult:
        if row.notify_attempts >= MAX_NOTIFY_ATTEMPTS:
            return await self._finish(row.id, NotificationOutcome.GIVEN_UP)
        now = self._clock.now()
        async with self._uows() as uow:
            changed = await uow.voice_jobs.defer_notification(
                row.id, now + timedelta(seconds=seconds), now
            )
            stored = await uow.voice_jobs.get(row.id) if changed else None
            await uow.commit()
        if stored is None or stored.notify_not_before is None:
            return Dropped()
        delay = max(0, math.ceil((stored.notify_not_before - self._clock.now()).total_seconds()))
        await self._enqueue(NotificationJob.from_voice_job(row), delay)
        return Deferred(delay)

    async def _retry(self, row: VoiceJob, error: Exception) -> NotifyResult:
        logger.warning("Voice notification failed (%s)", type(error).__name__)
        if row.notify_attempts >= MAX_NOTIFY_ATTEMPTS:
            return await self._finish(row.id, NotificationOutcome.GIVEN_UP)
        delay = retry_delay_seconds(row.notify_attempts)
        await self._enqueue(NotificationJob.from_voice_job(row), delay)
        return Retrying(delay)

    async def _enqueue(self, job: NotificationJob, delay: int) -> None:
        try:
            await self._queue.enqueue(job, delay_seconds=delay)
        except Exception as error:
            logger.warning("Notification enqueue failed (%s)", type(error).__name__)
