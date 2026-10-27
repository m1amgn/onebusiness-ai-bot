import asyncio
import logging
from dataclasses import dataclass

from taskbot.application.create_tasks import CreatedMessage, CreateTasksFromMessage
from taskbot.application.publishing import publish_events
from taskbot.application.replies import RETRYING
from taskbot.domain.audio import opus_duration_seconds
from taskbot.domain.errors import (
    EmptyMessageError,
    TranscriptionError,
    TransientTranscriptionError,
    UnreadableAudioError,
    VoiceFileTooLargeError,
    VoiceFileUnavailableError,
)
from taskbot.domain.extraction import Extraction
from taskbot.domain.jobs import NotificationJob, TranscriptionJob
from taskbot.domain.limits import (
    ATTEMPT_BUDGET_SECONDS,
    MAX_TRANSCRIPTION_ATTEMPTS,
    MAX_VOICE_BYTES,
    MAX_VOICE_SECONDS,
    TRANSCRIPT_MAX,
    VOICE_DURATION_TOLERANCE_SECONDS,
    retry_delay_seconds,
)
from taskbot.domain.messages import MessageKind
from taskbot.domain.ports.messaging import EventPublisher, JobQueue
from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.domain.ports.services import Clock, TaskExtractor
from taskbot.domain.ports.telegram import UserNotifier, VoiceFileSource
from taskbot.domain.ports.transcription import Transcriber
from taskbot.domain.text import MessageText
from taskbot.domain.voice_jobs import (
    CeilingReached,
    Claimed,
    ClaimOutcome,
    FailureKind,
    JobMissing,
    LeaseLive,
    VoiceJob,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AttemptProgress:
    attempt: int | None = None


@dataclass(frozen=True, slots=True)
class Done:
    job_id: int
    message_id: int


@dataclass(frozen=True, slots=True)
class Failed:
    job_id: int
    kind: FailureKind


@dataclass(frozen=True, slots=True)
class Retrying:
    job_id: int
    attempt: int
    delay_seconds: int


@dataclass(frozen=True, slots=True)
class Waiting:
    delay_seconds: int


@dataclass(frozen=True, slots=True)
class Stale:
    pass


@dataclass(frozen=True, slots=True)
class Dropped:
    pass


type TranscribeResult = Done | Failed | Retrying | Waiting | Stale | Dropped


@dataclass(frozen=True, slots=True)
class _Prepared:
    text: MessageText
    extraction: Extraction


@dataclass(frozen=True, slots=True)
class _RetryPreparation:
    pass


class TranscribeVoiceJob:
    def __init__(
        self,
        uows: UnitOfWorkFactory,
        create: CreateTasksFromMessage,
        files: VoiceFileSource,
        transcriber: Transcriber,
        extractor: TaskExtractor,
        notifier: UserNotifier,
        queue: JobQueue,
        publisher: EventPublisher,
        clock: Clock,
        interrupts: tuple[type[BaseException], ...] = (),
    ) -> None:
        self._uows = uows
        self._create = create
        self._files = files
        self._transcriber = transcriber
        self._extractor = extractor
        self._notifier = notifier
        self._queue = queue
        self._publisher = publisher
        self._clock = clock
        self._interrupts = interrupts

    async def execute(self, job: TranscriptionJob, progress: AttemptProgress) -> TranscribeResult:
        async with self._uows() as uow:
            claim = await uow.voice_jobs.claim(job.voice_job_id, self._clock.now())
            await uow.commit()
        if not isinstance(claim, Claimed):
            return await self._refused(job, claim)
        row = claim.job
        progress.attempt = row.attempts
        prepared = await self._prepare(row)
        if isinstance(prepared, FailureKind):
            return await self._fail(row.id, row.attempts, prepared)
        if isinstance(prepared, _RetryPreparation):
            return await self._retry(row)
        return await self._complete(row, prepared)

    async def time_out(self, voice_job_id: int, attempt: int) -> TranscribeResult:
        return await self._fail(voice_job_id, attempt, FailureKind.TRANSCRIPTION_TIMEOUT)

    async def _refused(self, job: TranscriptionJob, claim: ClaimOutcome) -> TranscribeResult:
        if isinstance(claim, LeaseLive):
            delay = max(claim.remaining_seconds, 1)
            await self._enqueue(job, delay)
            return Waiting(delay)
        if isinstance(claim, CeilingReached):
            async with self._uows() as uow:
                row = await uow.voice_jobs.exhaust(job.voice_job_id, self._clock.now())
                await uow.commit()
            if row is None:
                return Stale()
            await self._enqueue(NotificationJob.from_voice_job(row))
            return Failed(row.id, FailureKind.STUCK)
        if isinstance(claim, JobMissing):
            logger.info("Voice job missing: %s", job.voice_job_id)
        return Dropped()

    async def _prepare(self, row: VoiceJob) -> _Prepared | FailureKind | _RetryPreparation:
        budget = asyncio.timeout(ATTEMPT_BUDGET_SECONDS)
        try:
            async with budget:
                return await self._read(row)
        except self._interrupts:
            raise
        except TransientTranscriptionError, VoiceFileUnavailableError:
            return _RetryPreparation()
        except TranscriptionError as error:
            return error.kind
        except UnreadableAudioError:
            return FailureKind.UNSUPPORTED_AUDIO
        except VoiceFileTooLargeError:
            return FailureKind.TOO_LARGE
        except EmptyMessageError:
            return FailureKind.EMPTY_TRANSCRIPT
        except Exception as error:
            logger.warning("Voice preparation failed (%s)", type(error).__name__)
            if isinstance(error, TimeoutError) and budget.expired():
                return FailureKind.TRANSCRIPTION_TIMEOUT
            return FailureKind.INTERNAL

    async def _read(self, row: VoiceJob) -> _Prepared:
        audio = await self._files.fetch(row.file_id, max_bytes=MAX_VOICE_BYTES)
        maximum = MAX_VOICE_SECONDS + VOICE_DURATION_TOLERANCE_SECONDS
        if opus_duration_seconds(audio) > maximum:
            raise TranscriptionError(FailureKind.TOO_LONG)
        result = await self._transcriber.transcribe(audio)
        text, cut = MessageText.parse(result.text).bounded(TRANSCRIPT_MAX)
        if cut:
            logger.info("Voice transcript truncated: %s", row.id)
        extraction = await self._extractor.extract(text)
        return _Prepared(text, extraction)

    async def _complete(self, row: VoiceJob, prepared: _Prepared) -> TranscribeResult:
        async with self._uows() as uow:
            if await uow.voice_jobs.lock_attempt(row.id, row.attempts) is None:
                return Stale()
            created = await self._create.within(
                uow,
                user_id=row.user_id,
                ref=row.ref,
                kind=MessageKind.VOICE,
                text=prepared.text,
                extraction=prepared.extraction,
            )
            message_id = (
                created.message.id if isinstance(created, CreatedMessage) else created.message_id
            )
            completed = await uow.voice_jobs.complete(
                row.id, row.attempts, message_id, self._transcriber.provider, self._clock.now()
            )
            if completed is None:
                return Stale()
            events = await uow.commit()
        await publish_events(self._publisher, events)
        await self._enqueue(NotificationJob.from_voice_job(completed))
        return Done(row.id, message_id)

    async def _fail(self, job_id: int, attempt: int, kind: FailureKind) -> TranscribeResult:
        async with self._uows() as uow:
            row = await uow.voice_jobs.fail(
                job_id, attempt, kind, self._transcriber.provider, self._clock.now()
            )
            await uow.commit()
        if row is None:
            return Stale()
        await self._enqueue(NotificationJob.from_voice_job(row))
        return Failed(job_id, kind)

    async def _retry(self, row: VoiceJob) -> TranscribeResult:
        if row.attempts >= MAX_TRANSCRIPTION_ATTEMPTS:
            return await self._fail(row.id, row.attempts, FailureKind.TRANSIENT_EXHAUSTED)
        if row.attempts == 1 and row.ack_message_id is not None:
            try:
                await self._notifier.edit_message(row.ref.chat_id, row.ack_message_id, RETRYING)
            except self._interrupts:
                raise
            except Exception as error:
                logger.warning("Voice retry edit failed (%s)", type(error).__name__)
        async with self._uows() as uow:
            changed = await uow.voice_jobs.requeue(row.id, row.attempts, self._clock.now())
            await uow.commit()
        if changed is None:
            return Stale()
        delay = retry_delay_seconds(row.attempts)
        await self._enqueue(TranscriptionJob(voice_job_id=row.id), delay)
        return Retrying(row.id, row.attempts, delay)

    async def _enqueue(self, job: TranscriptionJob | NotificationJob, delay: int = 0) -> None:
        try:
            await self._queue.enqueue(job, delay_seconds=delay)
        except self._interrupts:
            raise
        except Exception as error:
            logger.warning("Voice enqueue failed (%s)", type(error).__name__)
