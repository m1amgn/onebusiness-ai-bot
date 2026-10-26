import time
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, cast

from fastapi import Depends, Request

from taskbot.application.change_task_status import ChangeTaskStatus
from taskbot.application.dashboard_token import AuthenticateToken
from taskbot.application.delete_task import DeleteTask
from taskbot.application.list_tasks import ListTasks
from taskbot.application.realtime_tickets import ConsumeRealtimeTicket, IssueRealtimeTicket
from taskbot.application.stream_events import StreamUserEvents
from taskbot.domain.ports.messaging import EventPublisher
from taskbot.infrastructure.clock import SystemClock
from taskbot.infrastructure.redis.client import create_redis_client
from taskbot.infrastructure.redis.events import RedisEventPublisher
from taskbot.infrastructure.redis.subscription import RedisEventSubscriber
from taskbot.infrastructure.redis.tickets import RedisTicketStore
from taskbot.infrastructure.settings.api import ApiSettings
from taskbot.infrastructure.sqlalchemy.engine import create_engine_and_sessions
from taskbot.infrastructure.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from taskbot.infrastructure.tokens import SecretsTokenGenerator, Sha256TokenHasher


@dataclass(frozen=True, slots=True)
class ApiServices:
    authenticate: AuthenticateToken
    list_tasks: ListTasks
    change_task_status: ChangeTaskStatus
    delete_task: DeleteTask
    issue_ticket: IssueRealtimeTicket
    consume_ticket: ConsumeRealtimeTicket
    stream_events: StreamUserEvents
    publisher: EventPublisher


@asynccontextmanager
async def open_api_services(settings: ApiSettings) -> AsyncIterator[ApiServices]:
    async with AsyncExitStack() as resources:
        engine, sessions = create_engine_and_sessions(str(settings.database_url))
        resources.push_async_callback(engine.dispose)
        client = create_redis_client(str(settings.redis_url), client_name="taskbot-api")
        resources.push_async_callback(client.aclose)

        def uows() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(sessions)

        namespace = settings.redis_namespace
        tickets = RedisTicketStore(client, SecretsTokenGenerator(), namespace)
        yield ApiServices(
            authenticate=AuthenticateToken(uows, Sha256TokenHasher()),
            list_tasks=ListTasks(uows),
            change_task_status=ChangeTaskStatus(uows, SystemClock()),
            delete_task=DeleteTask(uows),
            issue_ticket=IssueRealtimeTicket(tickets),
            consume_ticket=ConsumeRealtimeTicket(tickets, uows),
            stream_events=StreamUserEvents(
                RedisEventSubscriber(client, namespace), uows, time.monotonic
            ),
            publisher=RedisEventPublisher(client, namespace),
        )


def get_services(request: Request) -> ApiServices:
    # Starlette's State erases types at the application boundary.
    return cast(ApiServices, request.app.state.services)


type Services = Annotated[ApiServices, Depends(get_services)]
