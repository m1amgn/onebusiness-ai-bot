import asyncio
from contextlib import suppress

import pytest
from redis.exceptions import ConnectionError

from taskbot.infrastructure.redis.bot_lock import HeartbeatTask, LockKeeper, PollingLock
from taskbot.infrastructure.redis.client import create_redis_client
from tests.redis_sessions import RedisSession
from tests.tcp_proxy import tcp_proxy


async def test_silent_redis_bounds_renewal_and_shared_cleanup(redis_session: RedisSession) -> None:
    async with (
        tcp_proxy(redis_session.redis_url) as proxy,
        create_redis_client(proxy.url) as client,
    ):
        await client.ping()
        lock = PollingLock(client, redis_session.child(), ttl_seconds=0.5, call_timeout=0.04)
        assert await lock.acquire()
        keeper = LockKeeper(lock, renew_seconds=0.05, stop_after_seconds=0.15)
        proxy.freeze()
        async with asyncio.timeout(0.4):
            await keeper.run()
            assert keeper.lost
            await lock.release()
        proxy.unfreeze()


async def test_lock_exclusion_renewal_and_foreign_owner_cleanup(
    redis_session: RedisSession,
) -> None:
    namespace = redis_session.child()
    async with create_redis_client(redis_session.redis_url) as client:
        first = PollingLock(client, namespace, ttl_seconds=0.5)
        second = PollingLock(client, namespace, ttl_seconds=0.5)
        assert await first.acquire()
        assert not await second.acquire()
        await asyncio.sleep(0.03)
        await first.renew()
        assert await client.pttl(first.key) > 300
        assert await first.write_heartbeat()
        await client.delete(first.key)
        assert await second.acquire()
        assert await second.write_heartbeat()
        successor_health = await client.get(second.heartbeat_key)
        assert not await first.write_heartbeat()
        assert await client.get(second.heartbeat_key) == successor_health
        await first.release()
        assert await client.exists(second.key)
        assert await client.exists(second.heartbeat_key)
        await second.release()
        assert not await client.exists(second.key)
        assert not await client.exists(second.heartbeat_key)


async def test_keeper_renews_during_delayed_startup_then_reports_lost_key(
    redis_session: RedisSession,
) -> None:
    async with create_redis_client(redis_session.redis_url) as client:
        lock = PollingLock(client, redis_session.child(), ttl_seconds=0.3, call_timeout=0.05)
        assert await lock.acquire()
        keeper = LockKeeper(lock, renew_seconds=0.03, stop_after_seconds=0.12)
        task = asyncio.create_task(keeper.run())
        try:
            await asyncio.sleep(0.4)
            rival = PollingLock(client, lock.namespace, ttl_seconds=0.3)
            assert not await rival.acquire()
            await client.delete(lock.key)
            async with asyncio.timeout(0.3):
                await task
            assert keeper.lost
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            await lock.release()


async def test_heartbeat_starts_only_after_poll_health_and_cannot_block_renewal(
    redis_session: RedisSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with create_redis_client(redis_session.redis_url) as client:
        lock = PollingLock(client, redis_session.child(), ttl_seconds=0.25, call_timeout=0.1)
        assert await lock.acquire()
        keeper = LockKeeper(lock, renew_seconds=0.03, stop_after_seconds=0.12)
        healthy = False

        async def slow_heartbeat() -> bool:
            await asyncio.sleep(0.08)
            return await original()

        original = lock.write_heartbeat
        monkeypatch.setattr(lock, "write_heartbeat", slow_heartbeat)
        heartbeat = HeartbeatTask(
            lock, keeper, healthy=lambda: healthy, failed=lambda: False, interval_seconds=0.02
        )
        tasks = [asyncio.create_task(keeper.run()), asyncio.create_task(heartbeat.run())]
        try:
            await asyncio.sleep(0.05)
            assert not await client.exists(lock.heartbeat_key)
            healthy = True
            await asyncio.sleep(0.35)
            assert await client.exists(lock.heartbeat_key)
            assert await client.pttl(lock.key) > 100
            assert not keeper.lost
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await lock.release()


async def test_keeper_stops_before_ttl_after_failed_renewals(
    redis_session: RedisSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with create_redis_client(redis_session.redis_url) as client:
        await client.ping()
        lock = PollingLock(client, redis_session.child(), ttl_seconds=0.5, call_timeout=0.04)
        assert await lock.acquire()
        keeper = LockKeeper(lock, renew_seconds=0.05, stop_after_seconds=0.15)

        async def refused() -> None:
            await asyncio.sleep(0.04)
            raise ConnectionError("synthetic transport failure")

        monkeypatch.setattr(lock, "renew", refused)
        try:
            async with asyncio.timeout(0.4):
                await keeper.run()
            assert keeper.lost
            assert await client.pttl(lock.key) > 150
        finally:
            await lock.release()


@pytest.mark.parametrize("interruption", [TimeoutError, asyncio.CancelledError])
async def test_ambiguous_acquire_is_released_by_owner_token(
    redis_session: RedisSession, monkeypatch: pytest.MonkeyPatch, interruption: type[BaseException]
) -> None:
    async with create_redis_client(redis_session.redis_url) as client:
        lock = PollingLock(client, redis_session.child())
        original = lock._lock.acquire

        async def interrupted(*, token: str) -> bool:
            await original(token=token)
            raise interruption()

        monkeypatch.setattr(lock._lock, "acquire", interrupted)
        with pytest.raises(interruption):
            await lock.acquire()
        assert lock.acquired_at is None
        assert await client.exists(lock.key)
        await lock.release()
        assert not await client.exists(lock.key)
