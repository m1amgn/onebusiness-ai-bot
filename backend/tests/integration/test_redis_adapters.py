import asyncio
import time
from datetime import UTC, datetime

import pytest
from redis.exceptions import RedisError

from taskbot.application.dashboard_token import IssueDashboardToken
from taskbot.application.realtime_tickets import ConsumeRealtimeTicket
from taskbot.application.register_user import RegisterOrGetUser
from taskbot.domain.errors import InvalidTicketError
from taskbot.domain.events import AuthRevoked, TaskCreated, TaskView
from taskbot.domain.jobs import TicketClaims
from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.domain.tasks import TaskStatus
from taskbot.domain.users import TelegramIdentity
from taskbot.infrastructure.redis.client import create_redis_client, namespaced, user_channel
from taskbot.infrastructure.redis.events import RedisEventPublisher
from taskbot.infrastructure.redis.subscription import (
    EventStreamError,
    EventStreamGapError,
    subscribe_user_events,
)
from taskbot.infrastructure.redis.tickets import RedisTicketStore
from tests.fakes.clock import FakeClock
from tests.fakes.tokens import FakeTokenGenerator, FakeTokenHasher
from tests.redis_sessions import RedisSession
from tests.tcp_proxy import tcp_proxy


def event_for(user_id: int) -> TaskCreated:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return TaskCreated(
        user_id=user_id,
        task=TaskView(
            id=1,
            inbound_message_id=1,
            position=0,
            title="Synthetic task",
            description="",
            status=TaskStatus.PENDING,
            created_at=now,
            updated_at=now,
        ),
    )


async def test_event_routes_user_and_namespace_and_keeps_exact_json(
    redis_session: RedisSession,
) -> None:
    ns = redis_session.child()
    async with (
        create_redis_client(redis_session.redis_url) as client,
        subscribe_user_events(client, ns, 7) as a,
        subscribe_user_events(client, ns, 8) as b,
        subscribe_user_events(client, redis_session.child(), 7) as other,
    ):
        event = event_for(7)
        await RedisEventPublisher(client, ns).publish(event)
        assert await a.next(timeout_seconds=1) == event.model_dump_json()
        assert await b.next(timeout_seconds=1) is None
        assert await other.next(timeout_seconds=1) is None


async def test_entry_waits_for_confirmation_in_100_immediate_publishes(
    redis_session: RedisSession,
) -> None:
    ns = redis_session.child()
    async with create_redis_client(redis_session.redis_url) as client:
        for _ in range(100):
            async with subscribe_user_events(client, ns, 7) as subscription:
                await RedisEventPublisher(client, ns).publish(event_for(7))
                assert await subscription.next(timeout_seconds=1) == event_for(7).model_dump_json()


async def test_idle_reads_remain_healthy_beyond_socket_timeout(redis_session: RedisSession) -> None:
    async with (
        create_redis_client(redis_session.redis_url) as client,
        subscribe_user_events(client, redis_session.child(), 7) as subscription,
    ):
        started = time.monotonic()
        assert await subscription.next(timeout_seconds=0.5) is None
        assert time.monotonic() - started >= 0.45
        assert await subscription.next(timeout_seconds=7) is None


async def test_killed_subscriber_reports_a_gap(redis_session: RedisSession) -> None:
    ns = redis_session.child()
    name = ns + "subscriber"
    async with (
        create_redis_client(redis_session.redis_url, client_name=name) as client,
        create_redis_client(redis_session.redis_url) as control,
        subscribe_user_events(client, ns, 7) as subscription,
    ):
        connections = await control.client_list()
        owned = [c for c in connections if c["name"] == name and "P" in c["flags"]]
        assert len(owned) == 1
        await control.client_kill_filter(_id=owned[0]["id"])
        with pytest.raises(EventStreamGapError):
            await subscription.next(timeout_seconds=1)


async def test_frozen_subscriber_and_closed_port_use_stream_error(
    redis_session: RedisSession,
) -> None:
    async with (
        tcp_proxy(redis_session.redis_url) as proxy,
        create_redis_client(proxy.url) as client,
        subscribe_user_events(client, redis_session.child(), 7) as subscription,
    ):
        proxy.freeze()
        started = time.monotonic()
        try:
            with pytest.raises(EventStreamError):
                await subscription.next(timeout_seconds=0.1)
        finally:
            elapsed = time.monotonic() - started
            proxy.unfreeze()
        assert elapsed < 3.1
    async with create_redis_client("redis://127.0.0.1:1/0") as closed:
        with pytest.raises(EventStreamError):
            async with subscribe_user_events(closed, redis_session.child(), 7):
                pytest.fail("a closed port accepted a subscription")


async def test_publish_without_subscribers_and_bounded_failures(
    redis_session: RedisSession,
) -> None:
    ns = redis_session.child()
    async with create_redis_client(redis_session.redis_url) as client:
        await RedisEventPublisher(client, ns).publish(event_for(7))
        assert await client.pubsub_numsub(user_channel(ns, 7)) == [(user_channel(ns, 7), 0)]
    async with tcp_proxy(redis_session.redis_url) as proxy:
        proxy.freeze()
        for url in ("redis://127.0.0.1:1/0", proxy.url):
            async with create_redis_client(url) as client:
                started = time.monotonic()
                with pytest.raises((TimeoutError, RedisError)):
                    await RedisEventPublisher(client, ns).publish(AuthRevoked(user_id=7))
                assert time.monotonic() - started < 1.5


async def test_tickets_are_atomic_single_use_with_default_ttl(redis_session: RedisSession) -> None:
    ns = redis_session.child()
    async with create_redis_client(redis_session.redis_url) as client:
        store = RedisTicketStore(client, FakeTokenGenerator(), ns)
        claims = TicketClaims(user_id=7, token_version=3)
        ticket = await store.issue(claims)
        assert 29 <= await client.ttl(namespaced(ns, "ticket:" + ticket)) <= 30
        results = await asyncio.gather(*(store.consume(ticket) for _ in range(50)))
        assert results.count(claims) == 1
        assert results.count(None) == 49
        assert await store.consume(ticket) is None


async def test_expired_and_corrupt_tickets_are_consumed(
    redis_session: RedisSession, caplog: pytest.LogCaptureFixture
) -> None:
    ns = redis_session.child()
    async with create_redis_client(redis_session.redis_url) as client:
        store = RedisTicketStore(client, FakeTokenGenerator(), ns, ttl_seconds=1)
        ticket = await store.issue(TicketClaims(user_id=7, token_version=1))
        await asyncio.sleep(1.5)
        assert await store.consume(ticket) is None
        await client.set(namespaced(ns, "ticket:bad"), "not JSON")
        assert await store.consume("bad") is None
        assert await client.exists(namespaced(ns, "ticket:bad")) == 0
        assert len(caplog.records) == 1
        # redis-py shares a sync/async command annotation for LPUSH.
        await client.lpush(namespaced(ns, "ticket:wrongtype"), "x")  # type: ignore[misc]
        from redis.exceptions import ResponseError

        with pytest.raises(ResponseError):
            await store.consume("wrongtype")


async def test_postgresql_rotation_invalidates_real_redis_ticket(
    redis_session: RedisSession, postgres_uows: UnitOfWorkFactory
) -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    register = RegisterOrGetUser(postgres_uows, clock)
    issue = IssueDashboardToken(
        postgres_uows, register, FakeTokenGenerator(), FakeTokenHasher(), clock
    )
    identity = TelegramIdentity(7, "Synthetic user")
    first = await issue.execute(identity)
    async with create_redis_client(redis_session.redis_url) as client:
        tickets = RedisTicketStore(client, FakeTokenGenerator(), redis_session.child())
        ticket = await tickets.issue(TicketClaims(user_id=first.user_id, token_version=1))
        await issue.execute(identity)
        with pytest.raises(InvalidTicketError):
            await ConsumeRealtimeTicket(tickets, postgres_uows).execute(ticket)
        assert await tickets.consume(ticket) is None


async def test_frozen_subscription_entry_is_bounded(redis_session: RedisSession) -> None:
    async with (
        tcp_proxy(redis_session.redis_url) as proxy,
        create_redis_client(proxy.url) as client,
    ):
        proxy.freeze()
        subscription = subscribe_user_events(client, redis_session.child(), 7)
        started = time.monotonic()
        with pytest.raises(EventStreamError):
            await subscription.__aenter__()
        assert time.monotonic() - started < 7


@pytest.mark.parametrize("silent", [False, True])
async def test_failed_real_publish_stops_batch_after_one_attempt(
    redis_session: RedisSession,
    silent: bool,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    from taskbot.application.publishing import publish_events

    async with tcp_proxy(redis_session.redis_url) as proxy:
        proxy.freeze()
        url = proxy.url if silent else "redis://127.0.0.1:1/0"
        async with create_redis_client(url) as client:
            publisher = RedisEventPublisher(client, redis_session.child())
            recorded_publish = AsyncMock(wraps=publisher.publish)
            monkeypatch.setattr(publisher, "publish", recorded_publish)
            started = time.monotonic()
            await publish_events(publisher, (event_for(7), event_for(7), event_for(7)))
            assert time.monotonic() - started < 1.5
            assert recorded_publish.await_count == 1
            assert len(caplog.records) == 1
            assert "skipped 2" in caplog.records[0].getMessage()


async def test_invalid_utf8_ticket_is_consumed_and_logged_once(
    redis_session: RedisSession, caplog: pytest.LogCaptureFixture
) -> None:
    ns = redis_session.child()
    key = namespaced(ns, "ticket:badutf8")
    async with create_redis_client(redis_session.redis_url) as client:
        await client.set(key, b"\xff")
        store = RedisTicketStore(client, FakeTokenGenerator(), ns)
        assert await store.consume("badutf8") is None
        assert await client.exists(key) == 0
    assert len(caplog.records) == 1
    assert caplog.records[0].name == "taskbot.infrastructure.redis.tickets"
    assert caplog.records[0].getMessage() == "Discarded invalid realtime ticket claims"


@pytest.mark.parametrize("timeout_seconds", [1, 0], ids=["next", "idle_ping"])
async def test_invalid_utf8_event_uses_stream_error(
    redis_session: RedisSession, timeout_seconds: float
) -> None:
    ns = redis_session.child()
    async with (
        create_redis_client(redis_session.redis_url) as client,
        subscribe_user_events(client, ns, 7) as subscription,
    ):
        await client.publish(user_channel(ns, 7), b"\xff")
        # Zero skips the normal read loop and reads the queued invalid frame during idle PING.
        with pytest.raises(EventStreamError):
            await subscription.next(timeout_seconds=timeout_seconds)
