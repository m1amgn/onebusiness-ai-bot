import logging
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass, field

from billiard.exceptions import SoftTimeLimitExceeded
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from taskbot.application.create_tasks import CreateTasksFromMessage
from taskbot.application.notify_user import NotifyUser
from taskbot.application.recover_voice_jobs import RecoverStuckVoiceJobs
from taskbot.application.register_user import RegisterOrGetUser
from taskbot.application.transcribe_voice import TranscribeVoiceJob
from taskbot.domain.ports.services import Clock, TaskExtractor
from taskbot.domain.ports.telegram import UserNotifier, VoiceFileSource
from taskbot.domain.ports.transcription import Transcriber
from taskbot.infrastructure.celery.job_queue import CeleryJobQueue
from taskbot.infrastructure.clock import SystemClock
from taskbot.infrastructure.extraction.factory import open_extractor
from taskbot.infrastructure.redis.client import create_redis_client
from taskbot.infrastructure.redis.events import RedisEventPublisher
from taskbot.infrastructure.settings.worker import TranscribeWorkerSettings, WorkerSettings
from taskbot.infrastructure.sqlalchemy.engine import create_engine_and_sessions
from taskbot.infrastructure.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from taskbot.infrastructure.telegram.unconfigured import (
    UnconfiguredNotifier,
    UnconfiguredVoiceSource,
)
from taskbot.infrastructure.transcription.factory import open_transcriber

logger = logging.getLogger(__name__)
WORKER_EXTRACTION_DEADLINE_SECONDS = 30


@dataclass
class WorkerContainer:
    """Own resources as soon as they exist, including during a failed build."""

    settings: WorkerSettings
    engine: AsyncEngine | None = None
    sessions: async_sessionmaker[AsyncSession] | None = None
    redis: Redis | None = None
    publisher: RedisEventPublisher | None = None
    job_queue: CeleryJobQueue | None = None
    clock: Clock = field(default_factory=SystemClock)
    stack: AsyncExitStack = field(default_factory=AsyncExitStack)
    transcribe: TranscribeVoiceJob | None = None
    notify: NotifyUser | None = None
    recover: RecoverStuckVoiceJobs | None = None
    extractor: TaskExtractor | None = None
    transcriber: Transcriber | None = None
    files: VoiceFileSource | None = None
    notifier: UserNotifier | None = None

    def uows(self) -> SqlAlchemyUnitOfWork:
        if self.sessions is None:
            raise RuntimeError("Worker database factory is not initialized")
        return SqlAlchemyUnitOfWork(self.sessions)

    async def aclose(self) -> None:
        for resource in ("stack", "job_queue", "redis", "engine"):
            try:
                if resource == "stack":
                    await self.stack.aclose()
                elif resource == "job_queue" and self.job_queue is not None:
                    self.job_queue.close()
                elif resource == "redis" and self.redis is not None:
                    await self.redis.aclose()
                elif resource == "engine" and self.engine is not None:
                    await self.engine.dispose()
            except BaseException as error:
                with suppress(BaseException):
                    logger.error("Failed to close worker %s (%s)", resource, type(error).__name__)


async def build_container(container: WorkerContainer) -> None:
    from taskbot.entrypoints.worker import app

    settings = container.settings
    container.engine, container.sessions = create_engine_and_sessions(
        str(settings.database_url), pool_size=5
    )
    container.redis = create_redis_client(
        str(settings.redis_url), client_name=f"worker-{settings.worker_role}"
    )
    container.job_queue = CeleryJobQueue(app)
    if settings.telegram_bot_token is None:
        container.notifier = UnconfiguredNotifier()
        if settings.worker_role == "transcribe":
            container.files = UnconfiguredVoiceSource()
    else:
        from aiogram import Bot

        from taskbot.infrastructure.telegram.notifier import AiogramUserNotifier
        from taskbot.infrastructure.telegram.voice_files import AiogramVoiceFileSource

        bot = Bot(settings.telegram_bot_token.get_secret_value())
        container.stack.push_async_callback(bot.session.close)
        container.notifier = AiogramUserNotifier(bot)
        if settings.worker_role == "transcribe":
            container.files = AiogramVoiceFileSource(bot)
    if settings.worker_role == "notify":
        container.notify = NotifyUser(
            container.uows, container.notifier, container.job_queue, container.clock
        )
        container.recover = RecoverStuckVoiceJobs(
            container.uows, container.job_queue, container.clock
        )
        return
    if not isinstance(settings, TranscribeWorkerSettings):
        raise TypeError("Transcribe worker requires TranscribeWorkerSettings")
    container.publisher = RedisEventPublisher(container.redis, settings.redis_namespace)
    container.extractor = await container.stack.enter_async_context(
        open_extractor(
            settings,
            deadline_seconds=WORKER_EXTRACTION_DEADLINE_SECONDS,
            interrupts=(SoftTimeLimitExceeded,),
        )
    )
    container.transcriber = await container.stack.enter_async_context(open_transcriber(settings))
    create = CreateTasksFromMessage(
        container.uows,
        container.extractor,
        RegisterOrGetUser(container.uows, container.clock),
        container.clock,
    )
    assert container.files is not None
    container.transcribe = TranscribeVoiceJob(
        container.uows,
        create,
        container.files,
        container.transcriber,
        container.extractor,
        container.notifier,
        container.job_queue,
        container.publisher,
        container.clock,
        interrupts=(SoftTimeLimitExceeded,),
    )
