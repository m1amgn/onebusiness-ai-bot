from typing import Protocol

from taskbot.domain.events import DomainEvent
from taskbot.domain.jobs import NotificationJob, TicketClaims, TranscriptionJob


class JobQueue(Protocol):
    async def enqueue(
        self, job: TranscriptionJob | NotificationJob, *, delay_seconds: int = 0
    ) -> None: ...


class EventPublisher(Protocol):
    async def publish(self, event: DomainEvent) -> None: ...


class TicketStore(Protocol):
    async def issue(self, claims: TicketClaims) -> str: ...

    async def consume(self, ticket: str) -> TicketClaims | None: ...
