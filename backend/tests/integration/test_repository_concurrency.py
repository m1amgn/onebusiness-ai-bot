import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import asyncpg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from taskbot.domain.extraction import ExtractorName
from taskbot.domain.messages import MessageKind, MessageRef, NewInboundMessage
from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.domain.text import MessageText
from taskbot.domain.users import TelegramIdentity
from taskbot.domain.voice_jobs import Claimed, NewVoiceJob
from taskbot.infrastructure.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from tests.contract.helpers import create_voice
from tests.databases import quote_identifier, raw_url

NOW = datetime(2026, 1, 1, tzinfo=UTC)


async def locked_table_race[T](
    database_url: str, table: str, operation: Callable[[], Awaitable[T]]
) -> tuple[T, T]:
    """Let both writes reach the table lock; plain pre-check SELECTs can still run."""

    async def run() -> T:
        return await operation()

    control = await asyncpg.connect(raw_url(database_url))
    try:
        await control.execute("BEGIN")
        await control.execute(f"LOCK TABLE {quote_identifier(table)} IN EXCLUSIVE MODE")
        async with asyncio.TaskGroup() as group:
            left = group.create_task(run())
            right = group.create_task(run())
            try:
                async with asyncio.timeout(3):
                    while True:
                        assert not left.done()
                        assert not right.done()
                        waiting = await control.fetchval(
                            "SELECT count(*) FROM pg_locks WHERE relation=$1::regclass "
                            "AND database=(SELECT oid FROM pg_database "
                            "WHERE datname=current_database()) "
                            "AND NOT granted",
                            table,
                        )
                        if waiting == 2:
                            break
                        await asyncio.sleep(0.01)
            finally:
                await control.execute("COMMIT")
        return left.result(), right.result()
    finally:
        await control.close()


async def test_direct_message_inserts_resolve_the_same_conflict(
    postgres_uows: UnitOfWorkFactory, database_url: str
) -> None:
    async with postgres_uows() as uow:
        user = await uow.users.upsert(TelegramIdentity(7, "A"), NOW)
        await uow.commit()
    new = NewInboundMessage(
        user.user.id,
        MessageRef(7, 1),
        MessageKind.TEXT,
        MessageText.parse("Call home"),
        ExtractorName.HEURISTIC,
        False,
    )

    async def insert() -> int | None:
        async with postgres_uows() as uow:
            result = await uow.messages.add(new, NOW)
            await uow.commit()
            return None if result is None else result.id

    results = await locked_table_race(database_url, "inbound_messages", insert)
    assert sum(result is None for result in results) == 1
    async with postgres_uows() as uow:
        message = await uow.messages.get_by_ref(new.ref)
        assert message is not None
        assert message.id in results


async def test_direct_token_rotations_increment_atomically(
    postgres_uows: UnitOfWorkFactory, database_url: str
) -> None:
    async with postgres_uows() as uow:
        user = await uow.users.upsert(TelegramIdentity(7, "A"), NOW)
        await uow.commit()
    hashes = iter(("hash-left", "hash-right"))

    async def rotate() -> tuple[str, int | None]:
        token_hash = next(hashes)
        async with postgres_uows() as uow:
            version = await uow.users.rotate_token(user.user.id, token_hash, NOW)
            await uow.commit()
            return token_hash, version

    results = await locked_table_race(database_url, "users", rotate)
    assert {version for _, version in results} == {1, 2}
    async with postgres_uows() as uow:
        valid = [(value, await uow.users.find_by_token_hash(value)) for value, _ in results]
    winner, authenticated = next((value, auth) for value, auth in valid if auth is not None)
    assert sum(auth is not None for _, auth in valid) == 1
    assert authenticated is not None
    assert authenticated.token_version == 2
    assert (winner, 2) in results


async def test_lock_attempt_blocks_a_new_claim_until_completion_transaction_releases_it(
    postgres_uows: UnitOfWorkFactory,
    sql_sessions: async_sessionmaker[AsyncSession],
    database_url: str,
) -> None:
    async with postgres_uows() as uow:
        job = await create_voice(uow, NOW)
        await uow.voice_jobs.claim(job.id, NOW)
        await uow.commit()
    submitted = asyncio.Event()
    pid: int | None = None

    async def reclaim() -> bool:
        nonlocal pid
        async with SqlAlchemyUnitOfWork(sql_sessions) as waiting:
            pid = await waiting.session.scalar(text("SELECT pg_backend_pid()"))
            submitted.set()
            result = await waiting.voice_jobs.claim(job.id, NOW + timedelta(seconds=451))
            await waiting.commit()
            return isinstance(result, Claimed)

    monitor = await asyncpg.connect(raw_url(database_url))
    try:
        async with postgres_uows() as held:
            assert await held.voice_jobs.lock_attempt(job.id, 1) is not None
            async with asyncio.TaskGroup() as group:
                task = group.create_task(reclaim())
                try:
                    await submitted.wait()
                    async with asyncio.timeout(3):
                        while not await monitor.fetchval(
                            "SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=$1", pid
                        ):
                            assert not task.done()
                            await asyncio.sleep(0.01)
                    assert not task.done()
                finally:
                    await held.commit()
            assert task.result()
    finally:
        await monitor.close()


async def test_concurrent_sweeps_skip_held_batches_and_cap_each_at_one_hundred(
    postgres_uows: UnitOfWorkFactory,
) -> None:
    async with postgres_uows() as uow:
        user = await uow.users.upsert(TelegramIdentity(7, "A"), NOW)
        expected: set[int] = set()
        for message_id in range(150):
            result = await uow.voice_jobs.add_or_get(
                NewVoiceJob(user.user.id, MessageRef(7, message_id), "f", "f", 5, None), NOW
            )
            expected.add(result.job.id)
        await uow.commit()
    stamped = asyncio.Event()
    release = asyncio.Event()
    later = NOW + timedelta(seconds=461)

    async def first() -> list[int]:
        async with postgres_uows() as uow:
            rows = await uow.voice_jobs.stamp_stale_queued(later)
            stamped.set()
            await release.wait()
            await uow.commit()
            return rows

    async def second() -> list[int]:
        await stamped.wait()
        try:
            async with postgres_uows() as uow:
                rows = await asyncio.wait_for(uow.voice_jobs.stamp_stale_queued(later), 3)
                await uow.commit()
                return rows
        finally:
            release.set()

    async with asyncio.TaskGroup() as group:
        a = group.create_task(first())
        b = group.create_task(second())
    assert len(a.result()) == 100
    assert 0 < len(b.result()) <= 100
    assert set(a.result()).isdisjoint(b.result())
    assert set(a.result() + b.result()) == expected
    async with postgres_uows() as uow:
        assert await uow.voice_jobs.stamp_stale_queued(later) == []
