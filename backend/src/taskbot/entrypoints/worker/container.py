import logging
from contextlib import suppress
from dataclasses import dataclass, field

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from taskbot.infrastructure.celery.job_queue import CeleryJobQueue
from taskbot.infrastructure.clock import SystemClock
from taskbot.infrastructure.redis.client import create_redis_client
from taskbot.infrastructure.redis.events import RedisEventPublisher
from taskbot.infrastructure.settings.worker import WorkerSettings
from taskbot.infrastructure.sqlalchemy.engine import create_engine_and_sessions
from taskbot.infrastructure.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

logger = logging.getLogger(__name__)


@dataclass
class WorkerContainer:
    """Own resources as soon as they exist, including during a failed build."""

    settings: WorkerSettings
    engine: AsyncEngine | None = None
    sessions: async_sessionmaker[AsyncSession] | None = None
    redis: Redis | None = None
    publisher: RedisEventPublisher | None = None
    job_queue: CeleryJobQueue | None = None
    clock: SystemClock = field(default_factory=SystemClock)

    def uows(self) -> SqlAlchemyUnitOfWork:
        if self.sessions is None:
            raise RuntimeError("Worker database factory is not initialized")
        return SqlAlchemyUnitOfWork(self.sessions)

    async def aclose(self) -> None:
        # The synchronous queue close drains producers before loop-owned clients close.
        for resource in ("job_queue", "redis", "engine"):
            try:
                if resource == "job_queue" and self.job_queue is not None:
                    self.job_queue.close()
                elif resource == "redis" and self.redis is not None:
                    await self.redis.aclose()
                elif resource == "engine" and self.engine is not None:
                    await self.engine.dispose()
            except BaseException:
                with suppress(BaseException):
                    logger.exception("Failed to close worker %s", resource)


def build_container(container: WorkerContainer) -> None:
    from taskbot.entrypoints.worker import app

    settings = container.settings
    container.engine, container.sessions = create_engine_and_sessions(str(settings.database_url))
    container.redis = create_redis_client(str(settings.redis_url))
    container.publisher = RedisEventPublisher(container.redis, settings.redis_namespace)
    container.job_queue = CeleryJobQueue(app)
