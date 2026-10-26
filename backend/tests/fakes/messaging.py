from dataclasses import dataclass, field

from taskbot.domain.errors import EnqueueError
from taskbot.domain.events import DomainEvent
from taskbot.domain.jobs import NotificationJob, TicketClaims, TranscriptionJob
from tests.fakes.calls import Calls


@dataclass
class FakeJobQueue:
    calls: Calls = field(default_factory=Calls)
    jobs: list[tuple[TranscriptionJob | NotificationJob, int]] = field(default_factory=list)
    fail: bool = False

    async def enqueue(
        self, job: TranscriptionJob | NotificationJob, *, delay_seconds: int = 0
    ) -> None:
        self.calls.items.append("queue.enqueue")
        if self.fail:
            raise EnqueueError("Synthetic broker failure")
        self.jobs.append((job, delay_seconds))


@dataclass
class FakeEventPublisher:
    calls: Calls = field(default_factory=Calls)
    events: list[DomainEvent] = field(default_factory=list)
    fail: bool = False

    async def publish(self, event: DomainEvent) -> None:
        self.calls.items.append("events.publish")
        if self.fail:
            raise RuntimeError("publisher down")
        self.events.append(event)


@dataclass
class FakeTicketStore:
    calls: Calls = field(default_factory=Calls)
    tickets: dict[str, TicketClaims] = field(default_factory=dict)
    count: int = 0

    async def issue(self, claims: TicketClaims) -> str:
        self.calls.items.append("tickets.issue")
        self.count += 1
        ticket = f"ticket-{self.count}"
        self.tickets[ticket] = claims
        return ticket

    async def consume(self, ticket: str) -> TicketClaims | None:
        self.calls.items.append("tickets.consume")
        return self.tickets.pop(ticket, None)
