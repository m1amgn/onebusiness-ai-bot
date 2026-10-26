from dataclasses import dataclass
from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient

from taskbot.application.change_task_status import ChangeTaskStatus
from taskbot.application.create_tasks import (
    CreateTasksCommand,
    CreateTasksFromMessage,
    DuplicateMessage,
)
from taskbot.application.dashboard_token import AuthenticateToken, IssueDashboardToken
from taskbot.application.delete_task import DeleteTask
from taskbot.application.list_tasks import ListTasks
from taskbot.application.realtime_tickets import ConsumeRealtimeTicket, IssueRealtimeTicket
from taskbot.application.register_user import RegisterOrGetUser
from taskbot.application.stream_events import StreamUserEvents
from taskbot.domain.messages import MessageKind, MessageRef
from taskbot.domain.users import TelegramIdentity
from taskbot.entrypoints.api.app import create_app
from taskbot.entrypoints.api.container import ApiServices
from taskbot.infrastructure.extraction.heuristic import HeuristicExtractor
from taskbot.infrastructure.tokens import Sha256TokenHasher
from tests.fakes.clock import FakeClock
from tests.fakes.messaging import FakeEventPublisher, FakeTicketStore
from tests.fakes.persistence import FakeStore
from tests.fakes.stream import FakeElapsed, FakeEventSubscriber, FakeEventSubscription
from tests.fakes.tokens import FakeTokenGenerator


@dataclass(frozen=True)
class ApiFixture:
    services: ApiServices
    store: FakeStore
    publisher: FakeEventPublisher
    token: str
    user_id: int
    task_id: int


async def fake_api() -> ApiFixture:

    store = FakeStore()
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    register = RegisterOrGetUser(store, clock)
    create = CreateTasksFromMessage(store, HeuristicExtractor(), register, clock)
    result = await create.execute(
        CreateTasksCommand(
            TelegramIdentity(7, "A"), MessageRef(7, 1), MessageKind.TEXT, "Call home"
        )
    )
    assert not isinstance(result, DuplicateMessage)
    token = await IssueDashboardToken(
        store, register, FakeTokenGenerator(), Sha256TokenHasher(), clock
    ).execute(TelegramIdentity(7, "A"))
    publisher = FakeEventPublisher(calls=store.calls)
    tickets = FakeTicketStore()
    elapsed = FakeElapsed()
    subscriber = FakeEventSubscriber(FakeEventSubscription(elapsed))
    services = ApiServices(
        authenticate=AuthenticateToken(store, Sha256TokenHasher()),
        list_tasks=ListTasks(store),
        change_task_status=ChangeTaskStatus(store, clock),
        delete_task=DeleteTask(store),
        issue_ticket=IssueRealtimeTicket(tickets),
        consume_ticket=ConsumeRealtimeTicket(tickets, store),
        stream_events=StreamUserEvents(subscriber, store, elapsed),
        publisher=publisher,
    )
    return ApiFixture(services, store, publisher, token.token, result.user_id, result.tasks[0].id)


def api_client(fixture: ApiFixture, *, token: str | None) -> AsyncClient:
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    return AsyncClient(
        transport=ASGITransport(app=create_app(fixture.services)),
        base_url="http://test",
        headers=headers,
    )
