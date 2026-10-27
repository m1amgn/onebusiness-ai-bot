import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from celery import Celery
from kombu.exceptions import OperationalError

from taskbot.domain.errors import EnqueueError
from taskbot.domain.jobs import NotificationJob, TranscriptionJob
from taskbot.domain.limits import MAX_COUNTDOWN_SECONDS

logger = logging.getLogger(__name__)


class CeleryJobQueue:
    def __init__(self, app: Celery) -> None:
        self._app = app
        # Celery's first producer-pool lookup is lazy and is not thread-safe.
        _ = app.producer_pool
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="enqueue")

    async def enqueue(
        self, job: TranscriptionJob | NotificationJob, *, delay_seconds: int = 0
    ) -> None:
        if delay_seconds < 0:
            raise ValueError("delay_seconds must not be negative")
        if delay_seconds > MAX_COUNTDOWN_SECONDS:
            logger.debug(
                "Clamping job countdown from %s to %s seconds", delay_seconds, MAX_COUNTDOWN_SECONDS
            )
            delay_seconds = MAX_COUNTDOWN_SECONDS
        send = partial(
            self._app.send_task,
            job.name,
            kwargs=job.model_dump(mode="json"),
            queue=job.queue,
            countdown=delay_seconds or None,
        )
        try:
            await asyncio.get_running_loop().run_in_executor(self._executor, send)
        except OperationalError as error:
            raise EnqueueError(f"Failed to enqueue {job.name}") from error

    def close(self) -> None:
        """Drain producer threads before child shutdown closes loop-owned clients."""
        self._executor.shutdown(wait=True, cancel_futures=True)
        logger.info("Producer threads drained and closed")
