import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace

import httpx
import pytest
import pytest_asyncio
from pydantic import PostgresDsn, RedisDsn

from taskbot.application import stream_events
from taskbot.application.dashboard_token import IssueDashboardToken, IssuedToken
from taskbot.application.publishing import publish_events
from taskbot.application.realtime_tickets import ConsumeRealtimeTicket
from taskbot.application.register_user import RegisterOrGetUser
from taskbot.application.stream_events import StreamUserEvents
from taskbot.domain.events import AuthRevoked, TaskDeleted
from taskbot.domain.jobs import TicketClaims
from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.domain.users import TelegramIdentity
from taskbot.entrypoints.api.app import create_app
from taskbot.entrypoints.api.container import ApiServices, open_api_services
from taskbot.infrastructure.clock import SystemClock
from taskbot.infrastructure.redis.client import create_redis_client, user_channel
from taskbot.infrastructure.redis.subscription import RedisEventSubscriber
from taskbot.infrastructure.settings.api import ApiSettings
from taskbot.infrastructure.tokens import SecretsTokenGenerator, Sha256TokenHasher
from tests.api_server import api_server, assert_stream_headers, assert_ticket_rejected, read_frame
from tests.fakes.messaging import FakeTicketStore
from tests.redis_sessions import RedisSession

IDENTITY = TelegramIdentity(7101, "Synthetic realtime user")


@dataclass(frozen=True)
class RealtimeApi:
    client: httpx.AsyncClient
    services: ApiServices
    issue: IssueDashboardToken
    issued: IssuedToken
    namespace: str

    async def ticket(self) -> str:
        response = await self.client.post(
            "/api/realtime/tickets", headers={"Authorization": f"Bearer {self.issued.token}"}
        )
        assert response.status_code == 201
        payload = response.json()
        assert isinstance(payload, dict)
        ticket = payload["ticket"]
        assert isinstance(ticket, str)
        assert ticket
        assert payload == {"ticket": ticket, "expires_in": 30}
        return ticket


@pytest_asyncio.fixture
async def realtime_api(
    postgres_uows: UnitOfWorkFactory, database_url: str, redis_session: RedisSession
) -> AsyncIterator[RealtimeApi]:

    clock = SystemClock()
    issue = IssueDashboardToken(
        postgres_uows,
        RegisterOrGetUser(postgres_uows, clock),
        SecretsTokenGenerator(),
        Sha256TokenHasher(),
        clock,
    )
    issued = await issue.execute(IDENTITY)
    namespace = redis_session.child()
    settings = ApiSettings(
        _env_file=None,
        database_url=PostgresDsn(database_url),
        redis_url=RedisDsn(redis_session.redis_url),
        redis_namespace=namespace,
    )
    async with (
        open_api_services(settings) as services,
        api_server(create_app(services=services)) as url,
        httpx.AsyncClient(base_url=url, timeout=3) as client,
    ):
        yield RealtimeApi(client, services, issue, issued, namespace)


async def test_ticket_is_single_use_and_missing_or_rotated_ticket_is_unauthorized(
    realtime_api: RealtimeApi,
) -> None:
    api = realtime_api
    ticket = await api.ticket()
    async with api.client.stream("GET", "/api/events", params={"ticket": ticket}) as response:
        assert_stream_headers(response)
        assert await read_frame(response.aiter_lines()) == (": connected",)
    await assert_ticket_rejected(api.client, ticket)
    stale = await api.ticket()
    await api.issue.execute(IDENTITY)
    await assert_ticket_rejected(api.client, stale)
    await assert_ticket_rejected(api.client)


async def test_subscription_is_ready_for_100_immediate_publishes(realtime_api: RealtimeApi) -> None:
    api = realtime_api
    for iteration in range(100):
        ticket = await api.ticket()
        async with api.client.stream("GET", "/api/events", params={"ticket": ticket}) as response:
            assert_stream_headers(response)
            event = TaskDeleted(user_id=api.issued.user_id, task_id=iteration + 1)
            await api.services.publisher.publish(event)
            lines = response.aiter_lines()
            assert await read_frame(lines) == (": connected",)
            assert await read_frame(lines) == ("data: " + event.model_dump_json(),)


async def test_stream_does_not_receive_another_users_event(realtime_api: RealtimeApi) -> None:
    api = realtime_api
    other = await api.issue.execute(TelegramIdentity(7102, "Synthetic other user"))
    ticket = await api.ticket()
    async with api.client.stream("GET", "/api/events", params={"ticket": ticket}) as response:
        assert_stream_headers(response)
        lines = response.aiter_lines()
        assert await read_frame(lines) == (": connected",)
        await api.services.publisher.publish(TaskDeleted(user_id=other.user_id, task_id=1))
        with pytest.raises(TimeoutError):
            await read_frame(lines, timeout_seconds=1)


async def test_unavailable_subscription_returns_503_before_stream_headers(
    realtime_api: RealtimeApi, postgres_uows: UnitOfWorkFactory
) -> None:

    api = realtime_api
    tickets = FakeTicketStore()
    ticket = await tickets.issue(
        TicketClaims(user_id=api.issued.user_id, token_version=api.issued.token_version)
    )
    async with create_redis_client("redis://127.0.0.1:1/0") as closed:
        services = replace(
            api.services,
            consume_ticket=ConsumeRealtimeTicket(tickets, postgres_uows),
            stream_events=StreamUserEvents(
                RedisEventSubscriber(closed, api.namespace), postgres_uows, time.monotonic
            ),
        )
        async with (
            api_server(create_app(services=services)) as url,
            httpx.AsyncClient(base_url=url, timeout=3) as client,
            client.stream("GET", "/api/events", params={"ticket": ticket}) as response,
        ):
            assert response.status_code == 503
            assert response.headers["retry-after"] == "5"
            await response.aread()
            assert response.json() == {"detail": "Real-time events are unavailable"}


@pytest.mark.parametrize("publish_rotation", [True, False], ids=["published", "suppressed"])
async def test_rotation_forwards_revocation_and_closes_stream(
    realtime_api: RealtimeApi, monkeypatch: pytest.MonkeyPatch, publish_rotation: bool
) -> None:

    api = realtime_api
    if not publish_rotation:
        monkeypatch.setattr(stream_events, "RECHECK_SECONDS", 1)
    ticket = await api.ticket()
    async with api.client.stream("GET", "/api/events", params={"ticket": ticket}) as response:
        assert_stream_headers(response)
        lines = response.aiter_lines()
        assert await read_frame(lines) == (": connected",)
        rotated = await api.issue.execute(IDENTITY)
        if publish_rotation:
            await publish_events(api.services.publisher, rotated.events)
        event = AuthRevoked(user_id=api.issued.user_id)
        assert await read_frame(lines) == ("data: " + event.model_dump_json(),)
        assert await read_frame(lines) is None


async def test_client_abort_before_first_event_removes_subscription(
    realtime_api: RealtimeApi, redis_session: RedisSession
) -> None:
    api = realtime_api
    ticket = await api.ticket()
    channel = user_channel(api.namespace, api.issued.user_id)
    async with create_redis_client(redis_session.redis_url) as control:
        async with api.client.stream("GET", "/api/events", params={"ticket": ticket}) as response:
            assert_stream_headers(response)
            assert await control.pubsub_numsub(channel) == [(channel, 1)]
        async with asyncio.timeout(2):
            for _ in range(200):
                if await control.pubsub_numsub(channel) == [(channel, 0)]:
                    return
                await asyncio.sleep(0.01)
        pytest.fail("Client abort left a Redis subscription")
