import asyncio
from collections import deque
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress

from redis.asyncio import Redis
from redis.asyncio.client import PubSub
from redis.exceptions import RedisError

from taskbot.domain.errors import EventStreamError as EventStreamError
from taskbot.domain.errors import EventStreamGapError as EventStreamGapError
from taskbot.domain.ports.messaging import EventSubscription
from taskbot.infrastructure.redis.client import user_channel


class RedisEventSubscriber:
    def __init__(self, client: Redis, namespace: str) -> None:
        self._client = client
        self._namespace = namespace

    def subscribe(self, user_id: int) -> AbstractAsyncContextManager[EventSubscription]:
        return subscribe_user_events(self._client, self._namespace, user_id)


class UserEventSubscription:
    def __init__(self, pubsub: PubSub, channel: str) -> None:
        self._pubsub = pubsub
        self._channel = channel
        self._pending: deque[str] = deque()

    async def confirm(self) -> None:
        async with asyncio.timeout(5):
            await self._pubsub.subscribe(self._channel)
            while True:
                message = await self._read(1)
                if (
                    message is not None
                    and message.get("type") == "subscribe"
                    and message.get("channel") == self._channel
                ):
                    return
                self._remember(message)

    async def _read(self, wait_seconds: float) -> dict[str, object] | None:
        try:
            message: dict[str, object] | None = await self._pubsub.get_message(timeout=wait_seconds)
        except UnicodeDecodeError as error:
            raise EventStreamError("Redis event stream contains invalid UTF-8") from error
        return message

    def _remember(self, message: dict[str, object] | None) -> None:
        if message is None:
            return
        if message.get("type") == "subscribe":
            raise EventStreamGapError("Redis reconnected during subscription")
        if message.get("type") == "message":
            payload = message.get("data")
            if not isinstance(payload, str):
                raise EventStreamError("Expected a decoded Redis event")
            self._pending.append(payload)

    async def _idle_check(self) -> None:
        async with asyncio.timeout(2):
            await self._pubsub.ping()
            while True:
                message = await self._read(1)
                if message is not None and message.get("type") == "pong":
                    return
                # Events can precede PONG on the same connection. Never consume them as a probe.
                self._remember(message)

    async def next(self, timeout_seconds: float) -> str | None:
        if self._pending:
            return self._pending.popleft()
        deadline = asyncio.get_running_loop().time() + max(0, timeout_seconds)
        try:
            try:
                async with asyncio.timeout_at(deadline):
                    while asyncio.get_running_loop().time() < deadline:
                        self._remember(
                            await self._read(min(1, deadline - asyncio.get_running_loop().time()))
                        )
                        if self._pending:
                            return self._pending.popleft()
            except TimeoutError:
                pass
            await self._idle_check()
            return self._pending.popleft() if self._pending else None
        except (RedisError, TimeoutError) as error:
            raise EventStreamError("Redis event stream is unavailable") from error

    async def close(self) -> None:
        # A separate task is outside the consumer's cancellation scope. Keep waiting
        # through repeated cancellation; the two operation timeouts still bound it.
        cleanup = asyncio.create_task(self._close_connection())
        cancellation: asyncio.CancelledError | None = None
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError as error:
                cancellation = cancellation or error
            except RedisError, TimeoutError:
                break
        if cancellation is not None:
            with suppress(RedisError, TimeoutError):
                cleanup.result()
            raise cancellation
        cleanup.result()

    async def _close_connection(self) -> None:
        try:
            async with asyncio.timeout(1):
                await self._pubsub.unsubscribe(self._channel)
        finally:
            async with asyncio.timeout(1):
                # redis-py 6.4 omits the return annotation on PubSub.aclose.
                await self._pubsub.aclose()  # type: ignore[no-untyped-call]


@asynccontextmanager
async def subscribe_user_events(
    client: Redis, namespace: str, user_id: int
) -> AsyncIterator[UserEventSubscription]:
    subscription = UserEventSubscription(client.pubsub(), user_channel(namespace, user_id))
    failed = False
    try:
        try:
            await subscription.confirm()
        except (RedisError, TimeoutError) as error:
            raise EventStreamError("Redis event subscription is unavailable") from error
        yield subscription
    except BaseException:
        failed = True
        raise
    finally:
        try:
            await subscription.close()
        except (RedisError, TimeoutError) as error:
            if not failed:
                raise EventStreamError("Redis event subscription could not close") from error
        except asyncio.CancelledError:
            if not failed:
                raise
