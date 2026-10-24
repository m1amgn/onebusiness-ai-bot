from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from taskbot.domain.events import DomainEvent
from taskbot.domain.ports.persistence import (
    InboundMessageRepository,
    TaskRepository,
    UserRepository,
    VoiceJobRepository,
)
from taskbot.infrastructure.sqlalchemy.messages import SqlAlchemyMessageRepository
from taskbot.infrastructure.sqlalchemy.tasks import SqlAlchemyTaskRepository
from taskbot.infrastructure.sqlalchemy.users import SqlAlchemyUserRepository
from taskbot.infrastructure.sqlalchemy.voice_jobs import SqlAlchemyVoiceJobRepository


class SqlAlchemyUnitOfWork:
    users: UserRepository
    messages: InboundMessageRepository
    tasks: TaskRepository
    voice_jobs: VoiceJobRepository
    session: AsyncSession

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self._events: list[DomainEvent] = []

    async def __aenter__(self) -> Self:
        self.session = self._sessions()
        self.users = SqlAlchemyUserRepository(self.session)
        self.messages = SqlAlchemyMessageRepository(self.session)
        self.tasks = SqlAlchemyTaskRepository(self.session)
        self.voice_jobs = SqlAlchemyVoiceJobRepository(self.session)
        return self

    def record(self, *events: DomainEvent) -> None:
        self._events.extend(events)

    async def commit(self) -> tuple[DomainEvent, ...]:
        await self.session.commit()
        events = tuple(self._events)
        self._events.clear()
        return events

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            await self.session.rollback()
        finally:
            self._events.clear()
            await self.session.close()
