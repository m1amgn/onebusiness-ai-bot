import asyncio
import logging
import secrets
import time
from collections.abc import Callable

from redis.asyncio import Redis
from redis.exceptions import LockNotOwnedError, RedisError

from taskbot.infrastructure.redis.client import namespaced

POLL_LOCK_KEY = "bot:poll"
HEARTBEAT_KEY = "heartbeat:bot"
LOCK_TTL_SECONDS = 30
LOCK_RENEW_SECONDS = 5
LOCK_STOP_AFTER_SECONDS = 15
REDIS_CALL_TIMEOUT_SECONDS = 4

logger = logging.getLogger(__name__)

WRITE_HEARTBEAT = """
if redis.call('get', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('psetex', KEYS[2], ARGV[2], ARGV[3])
return 1
"""
DELETE_HEARTBEAT = """
if redis.call('get', KEYS[1]) ~= ARGV[1] then return 0 end
return redis.call('del', KEYS[2])
"""


class PollingLock:
    def __init__(
        self,
        client: Redis,
        namespace: str,
        *,
        ttl_seconds: float = LOCK_TTL_SECONDS,
        call_timeout: float = REDIS_CALL_TIMEOUT_SECONDS,
    ) -> None:
        self._client = client
        self.namespace = namespace
        self.key = namespaced(namespace, POLL_LOCK_KEY)
        self.heartbeat_key = namespaced(namespace, HEARTBEAT_KEY)
        self.ttl_seconds = ttl_seconds
        self.call_timeout = call_timeout
        self.acquired_at: float | None = None
        self._token = secrets.token_hex(16)
        self._lock = client.lock(self.key, timeout=ttl_seconds, blocking=False, thread_local=False)
        self._write_heartbeat = client.register_script(WRITE_HEARTBEAT)
        self._delete_heartbeat = client.register_script(DELETE_HEARTBEAT)

    async def acquire(self) -> bool:
        started = asyncio.get_running_loop().time()
        async with asyncio.timeout(self.call_timeout):
            acquired = await self._lock.acquire(token=self._token)
        if acquired:
            self.acquired_at = started
        return bool(acquired)

    async def remaining_seconds(self) -> int:
        async with asyncio.timeout(self.call_timeout):
            return max(0, int(await self._client.ttl(self.key)))

    async def renew(self) -> None:
        async with asyncio.timeout(self.call_timeout):
            await self._lock.reacquire()

    async def write_heartbeat(self) -> bool:
        async with asyncio.timeout(self.call_timeout):
            result = await self._write_heartbeat(
                keys=[self.key, self.heartbeat_key],
                args=[self._token, max(1, int(self.ttl_seconds * 1000)), str(time.time())],
            )
        return bool(result)

    async def release(self) -> None:
        if self.acquired_at is None:
            return
        try:
            async with asyncio.timeout(self.call_timeout):
                # Delete health only for this owner; release has its own token comparison.
                await self._delete_heartbeat(
                    keys=[self.key, self.heartbeat_key], args=[self._token]
                )
                await self._lock.release()
        except (RedisError, TimeoutError) as error:
            logger.warning("Polling lock cleanup failed (%s)", type(error).__name__)


class LockKeeper:
    def __init__(
        self,
        lock: PollingLock,
        *,
        renew_seconds: float = LOCK_RENEW_SECONDS,
        stop_after_seconds: float = LOCK_STOP_AFTER_SECONDS,
    ) -> None:
        if lock.acquired_at is None:
            raise RuntimeError("Acquire the polling lock before starting its keeper")
        self._lock = lock
        self._renew_seconds = renew_seconds
        self._stop_after_seconds = stop_after_seconds
        self.last_renewed = lock.acquired_at
        self.lost = False

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        next_at = self.last_renewed + self._renew_seconds
        while not self.lost:
            await asyncio.sleep(max(0, next_at - loop.time()))
            started = loop.time()
            try:
                await self._lock.renew()
            except LockNotOwnedError:
                self.lost = True
            except RedisError, TimeoutError:
                pass
            else:
                self.last_renewed = started
            if loop.time() - self.last_renewed >= self._stop_after_seconds:
                self.lost = True
            next_at += self._renew_seconds
            # Keep the acquisition-based cadence; a slow call never queues missed slots.
            if next_at <= loop.time():
                next_at += (
                    int((loop.time() - next_at) / self._renew_seconds) + 1
                ) * self._renew_seconds


class HeartbeatTask:
    def __init__(
        self,
        lock: PollingLock,
        keeper: LockKeeper,
        *,
        healthy: Callable[[], bool],
        failed: Callable[[], bool],
        interval_seconds: float = 10,
    ) -> None:
        self._lock = lock
        self._keeper = keeper
        self._healthy = healthy
        self._failed = failed
        self._interval_seconds = interval_seconds

    async def run(self) -> None:
        while not self._keeper.lost and not self._failed():
            if self._healthy():
                try:
                    if not await self._lock.write_heartbeat():
                        self._keeper.lost = True
                        return
                except (RedisError, TimeoutError) as error:
                    logger.warning("Bot heartbeat failed (%s)", type(error).__name__)
            await asyncio.sleep(self._interval_seconds)
