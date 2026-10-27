import logging

from taskbot.domain.jobs import NotificationJob, TranscriptionJob
from taskbot.domain.ports.messaging import JobQueue
from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.domain.ports.services import Clock
from taskbot.domain.voice_jobs import VoiceJobStatus

logger = logging.getLogger(__name__)


class RecoverStuckVoiceJobs:
    def __init__(self, uows: UnitOfWorkFactory, queue: JobQueue, clock: Clock) -> None:
        self._uows = uows
        self._queue = queue
        self._clock = clock

    async def execute(self) -> None:
        now = self._clock.now()
        async with self._uows() as uow:
            processing = await uow.voice_jobs.stamp_stale_processing(now)
            queued = await uow.voice_jobs.stamp_stale_queued(now)
            unnotified = await uow.voice_jobs.stamp_unnotified(now)
            await uow.commit()
        jobs: list[TranscriptionJob | NotificationJob] = [
            TranscriptionJob(voice_job_id=job_id) for job_id in (*processing, *queued)
        ]
        jobs.extend(
            NotificationJob(
                voice_job_id=row.id,
                status="done" if row.status == VoiceJobStatus.DONE else "failed",
            )
            for row in unnotified
        )
        for job in jobs:
            try:
                await self._queue.enqueue(job)
            except Exception as error:
                logger.warning("Recovered voice enqueue failed (%s)", type(error).__name__)
