import asyncio
import time

import anyio
import pytest
from redis.asyncio import Redis
from redis.asyncio.client import PubSub

from taskbot.infrastructure.redis.client import create_redis_client
from taskbot.infrastructure.redis.subscription import subscribe_user_events
from tests.redis_sessions import RedisSession


async def consume_stream(
    client: Redis,
    pubsub: PubSub,
    namespace: str,
    ready: asyncio.Event,
    closing: asyncio.Event,
    errors: list[asyncio.CancelledError],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    try:
        async with subscribe_user_events(client, namespace, 7):
            assert pubsub.connection is not None
            writer = pubsub.connection._writer
            assert writer is not None
            original_close = writer.wait_closed

            async def suspended_close() -> None:
                closing.set()
                await asyncio.sleep(0.02)
                await original_close()

            monkeypatch.setattr(writer, "wait_closed", suspended_close)
            ready.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError as error:
                errors.append(error)
                raise
    except asyncio.CancelledError as error:
        errors.append(error)
        raise


@pytest.mark.parametrize("cancellation", ["anyio", "repeated"])
@pytest.mark.parametrize("stalled_unsubscribe", [False, True])
async def test_cancelled_stream_releases_real_pubsub_connection(
    redis_session: RedisSession,
    monkeypatch: pytest.MonkeyPatch,
    cancellation: str,
    stalled_unsubscribe: bool,
) -> None:
    async with create_redis_client(redis_session.redis_url) as client:
        pubsub = client.pubsub()
        monkeypatch.setattr(client, "pubsub", lambda: pubsub)
        ready, closing = asyncio.Event(), asyncio.Event()
        errors: list[asyncio.CancelledError] = []
        if stalled_unsubscribe:

            async def unsubscribe(*channels: str) -> None:
                await asyncio.Event().wait()

            monkeypatch.setattr(pubsub, "unsubscribe", unsubscribe)
        arguments = (client, pubsub, redis_session.child(), ready, closing, errors, monkeypatch)
        started = time.monotonic()
        try:
            async with asyncio.timeout(3):
                if cancellation == "anyio":
                    async with anyio.create_task_group() as group:
                        group.start_soon(consume_stream, *arguments)
                        await ready.wait()
                        group.cancel_scope.cancel()
                else:
                    task = asyncio.create_task(consume_stream(*arguments))
                    await ready.wait()
                    task.cancel("original disconnect")
                    await closing.wait()
                    task.cancel("second disconnect")
                    with pytest.raises(asyncio.CancelledError):
                        await task
            assert not client.connection_pool._in_use_connections
            assert pubsub.connection is None
            assert len(errors) == 2
            assert errors[0] is errors[1]
            assert time.monotonic() - started < 2.5
        finally:
            # Independent cleanup also releases the deliberately leaking baseline.
            # redis-py 6.4 omits the return annotation on PubSub.aclose.
            await pubsub.aclose()  # type: ignore[no-untyped-call]
