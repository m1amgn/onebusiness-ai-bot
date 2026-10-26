from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from redis.asyncio import Redis

from taskbot.domain.events import AuthRevoked
from taskbot.domain.jobs import TicketClaims
from taskbot.infrastructure.redis.client import namespaced, user_channel
from taskbot.infrastructure.redis.events import RedisEventPublisher
from taskbot.infrastructure.redis.subscription import EventStreamError, subscribe_user_events
from taskbot.infrastructure.redis.tickets import RedisTicketStore
from tests.fakes.tokens import FakeTokenGenerator


async def test_all_adapter_names_carry_the_namespace() -> None:
    client = Mock(spec=Redis)
    client.publish = AsyncMock(return_value=0)
    client.set = AsyncMock(return_value=True)
    client.getdel = AsyncMock(return_value=None)
    pubsub = Mock()
    pubsub.subscribe = AsyncMock()
    pubsub.unsubscribe = AsyncMock()
    pubsub.aclose = AsyncMock()
    pubsub.get_message = AsyncMock(
        return_value={"type": "subscribe", "channel": "ns:events:user:7"}
    )
    client.pubsub.return_value = pubsub
    redis = cast(Redis, client)
    event = AuthRevoked(user_id=7)
    await RedisEventPublisher(redis, "ns:").publish(event)
    client.publish.assert_awaited_once_with("ns:events:user:7", event.model_dump_json())
    tickets = RedisTicketStore(redis, FakeTokenGenerator(), "ns:")
    ticket = await tickets.issue(TicketClaims(user_id=7, token_version=2))
    client.set.assert_awaited_once_with(
        "ns:ticket:" + ticket, '{"user_id":7,"token_version":2}', ex=30, nx=True
    )
    await tickets.consume(ticket)
    client.getdel.assert_awaited_once_with("ns:ticket:" + ticket)
    async with subscribe_user_events(redis, "ns:", 7):
        pubsub.subscribe.assert_awaited_once_with("ns:events:user:7")
    pubsub.unsubscribe.assert_awaited_once_with("ns:events:user:7")
    assert namespaced("ns:", "lock") == "ns:lock"
    assert user_channel("ns:", 7) == "ns:events:user:7"


@pytest.mark.parametrize("ticket", ["", "x" * 65, "界", "bad+ticket", "bad/ticket"])
async def test_invalid_ticket_does_not_call_redis(ticket: str) -> None:
    client = Mock(spec=Redis)
    store = RedisTicketStore(cast(Redis, client), FakeTokenGenerator(), "ns:")
    assert await store.consume(ticket) is None
    assert client.mock_calls == []


async def test_ticket_collision_is_refused() -> None:
    client = Mock(spec=Redis)
    client.set = AsyncMock(return_value=None)
    store = RedisTicketStore(cast(Redis, client), FakeTokenGenerator(), "ns:")
    with pytest.raises(RuntimeError):
        await store.issue(TicketClaims(user_id=1, token_version=1))


async def test_subscription_preserves_event_received_during_ping() -> None:
    client = Mock(spec=Redis)
    pubsub = Mock()
    client.pubsub.return_value = pubsub
    pubsub.subscribe = AsyncMock()
    pubsub.unsubscribe = AsyncMock()
    pubsub.aclose = AsyncMock()
    pubsub.ping = AsyncMock()
    pubsub.get_message = AsyncMock(
        side_effect=[
            {"type": "subscribe", "channel": "ns:events:user:7"},
            {"type": "message", "data": "interleaved"},
            {"type": "pong", "data": ""},
        ]
    )
    async with subscribe_user_events(cast(Redis, client), "ns:", 7) as subscription:
        assert await subscription.next(timeout_seconds=0) == "interleaved"
    pubsub.ping.assert_awaited_once()


async def test_subscription_exit_errors_follow_stream_contract() -> None:
    from redis.exceptions import ConnectionError

    client = Mock(spec=Redis)
    pubsub = Mock()
    client.pubsub.return_value = pubsub
    pubsub.subscribe = AsyncMock()
    pubsub.unsubscribe = AsyncMock(side_effect=ConnectionError("lost"))
    pubsub.aclose = AsyncMock()
    pubsub.get_message = AsyncMock(
        return_value={"type": "subscribe", "channel": "ns:events:user:7"}
    )
    with pytest.raises(EventStreamError):
        async with subscribe_user_events(cast(Redis, client), "ns:", 7):
            pass
    pubsub.aclose.assert_awaited_once()


@pytest.mark.parametrize("phase", ["subscribe", "read", "aclose", "gap_and_close"])
async def test_redis_errors_are_wrapped_without_masking_a_stream_gap(phase: str) -> None:
    from redis.exceptions import ConnectionError

    from taskbot.infrastructure.redis.subscription import EventStreamGapError

    client = Mock(spec=Redis)
    pubsub = Mock()
    client.pubsub.return_value = pubsub
    pubsub.subscribe = AsyncMock(
        side_effect=ConnectionError("entry") if phase == "subscribe" else None
    )
    pubsub.unsubscribe = AsyncMock(
        side_effect=ConnectionError("exit") if phase == "gap_and_close" else None
    )
    pubsub.aclose = AsyncMock(side_effect=ConnectionError("close") if phase == "aclose" else None)
    confirmation = {"type": "subscribe", "channel": "ns:events:user:7"}
    if phase == "read":
        pubsub.get_message = AsyncMock(side_effect=[confirmation, ConnectionError("read")])
    else:
        pubsub.get_message = AsyncMock(side_effect=[confirmation, confirmation])

    async def open_stream() -> None:
        async with subscribe_user_events(cast(Redis, client), "ns:", 7) as subscription:
            if phase in {"read", "gap_and_close"}:
                await subscription.next(timeout_seconds=0.1)

    expected = EventStreamGapError if phase == "gap_and_close" else EventStreamError
    with pytest.raises(expected):
        await open_stream()


async def test_subscription_entry_wraps_decoding_errors() -> None:
    client = Mock(spec=Redis)
    pubsub = Mock()
    client.pubsub.return_value = pubsub
    pubsub.subscribe = AsyncMock()
    pubsub.unsubscribe = AsyncMock()
    pubsub.aclose = AsyncMock()
    # Redis's generated confirmation contains only our ASCII channel, so entry needs a double.
    pubsub.get_message = AsyncMock(
        side_effect=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    )
    stream = subscribe_user_events(cast(Redis, client), "ns:", 7)
    with pytest.raises(EventStreamError):
        await stream.__aenter__()
    pubsub.unsubscribe.assert_awaited_once()
    pubsub.aclose.assert_awaited_once()


async def test_subscription_read_cancellation_propagates_and_closes() -> None:
    import asyncio

    client = Mock(spec=Redis)
    pubsub = Mock()
    client.pubsub.return_value = pubsub
    pubsub.subscribe = AsyncMock()
    pubsub.unsubscribe = AsyncMock()
    pubsub.aclose = AsyncMock()
    reading = asyncio.Event()
    confirmation_pending = True

    async def read_message(**options: float) -> dict[str, object] | None:
        nonlocal confirmation_pending
        if confirmation_pending:
            confirmation_pending = False
            return {"type": "subscribe", "channel": "ns:events:user:7"}
        reading.set()
        await asyncio.Event().wait()
        return None

    pubsub.get_message = AsyncMock(side_effect=read_message)

    async def read_stream() -> None:
        async with subscribe_user_events(cast(Redis, client), "ns:", 7) as subscription:
            await subscription.next(timeout_seconds=10)

    consumer = asyncio.create_task(read_stream())
    try:
        await asyncio.wait_for(reading.wait(), timeout=1)
    finally:
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer
    pubsub.unsubscribe.assert_awaited_once()
    pubsub.aclose.assert_awaited_once()
