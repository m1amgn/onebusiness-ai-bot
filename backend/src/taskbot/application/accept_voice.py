import logging
from dataclasses import dataclass
from typing import Literal

from taskbot.application.register_user import RegisterOrGetUser
from taskbot.application.replies import VOICE_ACK
from taskbot.domain.jobs import TranscriptionJob
from taskbot.domain.limits import MAX_VOICE_BYTES, MAX_VOICE_SECONDS
from taskbot.domain.messages import MessageRef
from taskbot.domain.ports.messaging import JobQueue
from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.domain.ports.services import Clock
from taskbot.domain.ports.telegram import UserNotifier
from taskbot.domain.users import TelegramIdentity
from taskbot.domain.voice_jobs import NewVoiceJob, VoiceJob, VoiceJobStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AcceptVoiceCommand:
    identity: TelegramIdentity
    ref: MessageRef
    file_id: str
    file_unique_id: str
    duration_seconds: int
    file_size: int | None


@dataclass(frozen=True, slots=True)
class VoiceRefused:
    reason: Literal["too_long", "too_large"]


@dataclass(frozen=True, slots=True)
class VoiceAccepted:
    job_id: int
    acknowledged: bool
    enqueued: bool


@dataclass(frozen=True, slots=True)
class VoiceAlreadyAccepted:
    job_id: int


class AcceptVoiceMessage:
    def __init__(
        self,
        uows: UnitOfWorkFactory,
        register: RegisterOrGetUser,
        notifier: UserNotifier,
        queue: JobQueue,
        clock: Clock,
    ) -> None:
        self._uows = uows
        self._register = register
        self._notifier = notifier
        self._queue = queue
        self._clock = clock

    async def execute(
        self, command: AcceptVoiceCommand
    ) -> VoiceRefused | VoiceAccepted | VoiceAlreadyAccepted:
        if command.duration_seconds > MAX_VOICE_SECONDS:
            return VoiceRefused("too_long")
        if command.file_size is not None and command.file_size > MAX_VOICE_BYTES:
            return VoiceRefused("too_large")
        async with self._uows() as uow:
            registered = await self._register.within(uow, command.identity)
            added = await uow.voice_jobs.add_or_get(
                NewVoiceJob(
                    registered.user.id,
                    command.ref,
                    command.file_id,
                    command.file_unique_id,
                    command.duration_seconds,
                    command.file_size,
                ),
                self._clock.now(),
            )
            await uow.commit()
        job = added.job
        if not added.created and (
            job.status != VoiceJobStatus.QUEUED or job.ack_message_id is not None
        ):
            return VoiceAlreadyAccepted(job.id)
        acknowledged = await self._acknowledge(job)
        enqueued = await self._enqueue(job.id)
        return VoiceAccepted(job.id, acknowledged, enqueued)

    async def _acknowledge(self, job: VoiceJob) -> bool:
        try:
            ack = await self._notifier.send_reply(
                job.ref.chat_id, VOICE_ACK, reply_to_message_id=job.ref.message_id
            )
        except Exception as error:
            logger.warning("Voice acknowledgement failed (%s)", type(error).__name__)
            return False
        try:
            async with self._uows() as uow:
                stored = await uow.voice_jobs.set_ack_message_id(job.id, ack, self._clock.now())
                await uow.commit()
            if not stored:
                logger.info("Another handler stored the voice acknowledgement")
        except Exception as error:
            logger.warning("Acknowledgement metadata failed (%s)", type(error).__name__)
        return True

    async def _enqueue(self, job_id: int) -> bool:
        try:
            await self._queue.enqueue(TranscriptionJob(voice_job_id=job_id))
        except Exception as error:
            logger.warning("Voice enqueue failed (%s)", type(error).__name__)
            return False
        try:
            async with self._uows() as uow:
                await uow.voice_jobs.mark_enqueued(job_id, self._clock.now())
                await uow.commit()
        except Exception as error:
            logger.warning("Enqueue metadata failed (%s)", type(error).__name__)
        return True
