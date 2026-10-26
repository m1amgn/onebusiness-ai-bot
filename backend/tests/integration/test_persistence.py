import asyncio
from dataclasses import is_dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from taskbot.application.change_task_status import ChangeTaskStatus
from taskbot.application.create_tasks import (
    CreateTasksCommand,
    CreateTasksFromMessage,
    DuplicateMessage,
)
from taskbot.application.dashboard_token import AuthenticateToken, IssueDashboardToken
from taskbot.application.list_tasks import ListTasks
from taskbot.application.register_user import RegisterOrGetUser
from taskbot.domain.errors import InvalidTokenError, StaleAttemptError, TaskNotFoundError
from taskbot.domain.extraction import Extraction, ExtractorName, TaskDraft
from taskbot.domain.messages import MessageKind, MessageRef
from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.domain.tasks import TaskStatus
from taskbot.domain.text import MessageText
from taskbot.domain.users import TelegramIdentity
from taskbot.domain.voice_jobs import Claimed, NewVoiceJob, TranscriptionProvider, VoiceJobStatus
from taskbot.infrastructure.sqlalchemy.tables import UserRow
from taskbot.infrastructure.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from tests.contract.helpers import all_tasks, create_message, create_voice
from tests.fakes.calls import Calls
from tests.fakes.clock import FakeClock
from tests.fakes.extraction import FakeExtractor
from tests.fakes.tokens import FakeTokenGenerator


async def test_engine_connects_to_the_owned_database(database_url: str) -> None:
    from taskbot.infrastructure.sqlalchemy.engine import create_engine_and_sessions

    engine, _ = create_engine_and_sessions(database_url, pool_size=2)
    try:
        async with engine.connect() as connection:
            from sqlalchemy import text

            assert str(await connection.scalar(text("SELECT current_database()"))).startswith(
                "taskbot_test_"
            )
    finally:
        await engine.dispose()


async def test_concurrent_first_contact_and_token_rotation(
    postgres_uows: UnitOfWorkFactory,
    sql_sessions: async_sessionmaker[AsyncSession],
) -> None:
    from taskbot.infrastructure.tokens import Sha256TokenHasher

    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    register = RegisterOrGetUser(postgres_uows, clock)
    identity = TelegramIdentity(7, "A")
    async with asyncio.TaskGroup() as group:
        a = group.create_task(register.execute(identity))
        b = group.create_task(register.execute(identity))
    assert a.result().user.id == b.result().user.id
    assert sum((a.result().created, b.result().created)) == 1
    hasher = Sha256TokenHasher()
    issue = IssueDashboardToken(postgres_uows, register, FakeTokenGenerator(), hasher, clock)
    async with asyncio.TaskGroup() as group:
        left = group.create_task(issue.execute(identity))
        right = group.create_task(issue.execute(identity))
    authenticate = AuthenticateToken(postgres_uows, hasher)
    valid = 0
    for issued in (left.result(), right.result()):
        try:
            authenticated = await authenticate.execute(issued.token)
        except InvalidTokenError:
            continue
        valid += 1
        assert authenticated.token_version == 2
    assert valid == 1
    async with sql_sessions() as session:
        stored = await session.scalar(select(UserRow.dashboard_token_hash))
        assert stored in {hasher.hash(left.result().token), hasher.hash(right.result().token)}
        assert stored not in {left.result().token, right.result().token}


async def test_concurrent_claims(postgres_uows: UnitOfWorkFactory) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    async with postgres_uows() as uow:
        job = await create_voice(uow, now)
        await uow.commit()

    async def claim() -> bool:
        async with postgres_uows() as uow:
            result = await uow.voice_jobs.claim(job.id, now)
            await uow.commit()
            return isinstance(result, Claimed)

    async with asyncio.TaskGroup() as group:
        attempts = [group.create_task(claim()) for _ in range(10)]
    assert sum(t.result() for t in attempts) == 1


@pytest.mark.parametrize("status", list(VoiceJobStatus))
async def test_concurrent_sweeps_never_return_the_same_row(
    postgres_uows: UnitOfWorkFactory, status: VoiceJobStatus
) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    async with postgres_uows() as uow:
        job = await create_voice(uow, now)
        if status != VoiceJobStatus.QUEUED:
            await uow.voice_jobs.claim(job.id, now)
        if status == VoiceJobStatus.DONE:
            mid = await create_message(uow, now)
            await uow.voice_jobs.complete(job.id, 1, mid, TranscriptionProvider.FAKE, now)
        if status == VoiceJobStatus.FAILED:
            from taskbot.domain.voice_jobs import FailureKind

            await uow.voice_jobs.fail(
                job.id, 1, FailureKind.EMPTY_TRANSCRIPT, TranscriptionProvider.FAKE, now
            )
        await uow.commit()

    async def sweep() -> list[int]:
        async with postgres_uows() as uow:
            later = now + timedelta(seconds=461)
            if status == VoiceJobStatus.QUEUED:
                result = await uow.voice_jobs.stamp_stale_queued(later)
            elif status == VoiceJobStatus.PROCESSING:
                result = await uow.voice_jobs.stamp_stale_processing(later)
            else:
                result = [row.id for row in await uow.voice_jobs.stamp_unnotified(later)]
            await uow.commit()
            return result

    async with asyncio.TaskGroup() as group:
        a = group.create_task(sweep())
        b = group.create_task(sweep())
    assert a.result() + b.result() == [job.id]


async def test_voice_composition_rolls_back_and_fences_stale_attempt(
    postgres_uows: UnitOfWorkFactory,
) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    clock = FakeClock(now)
    extraction = Extraction(
        task=TaskDraft(title="Call home"), extractor=ExtractorName.HEURISTIC, degraded=False
    )
    create = CreateTasksFromMessage(
        postgres_uows,
        FakeExtractor(extraction, Calls()),
        RegisterOrGetUser(postgres_uows, clock),
        clock,
    )
    async with postgres_uows() as uow:
        job = await create_voice(uow, now)
        await uow.voice_jobs.claim(job.id, now)
        await uow.commit()

    async def complete(attempt: int, fail: bool) -> None:
        async with postgres_uows() as uow:
            if await uow.voice_jobs.lock_attempt(job.id, attempt) is None:
                raise StaleAttemptError("stale")
            result = await create.within(
                uow,
                user_id=job.user_id,
                ref=job.ref,
                kind=MessageKind.VOICE,
                text=MessageText.parse("Call home"),
                extraction=extraction,
            )
            if fail:
                raise RuntimeError("forced rollback")
            message_id = (
                result.message_id if isinstance(result, DuplicateMessage) else result.message.id
            )
            assert (
                await uow.voice_jobs.complete(
                    job.id, attempt, message_id, TranscriptionProvider.FAKE, clock.now()
                )
                is not None
            )
            await uow.commit()

    with pytest.raises(RuntimeError, match="forced rollback"):
        await complete(1, True)
    async with postgres_uows() as uow:
        assert await uow.messages.get_by_ref(job.ref) is None
        assert await all_tasks(uow, job.user_id) == ()
        still = await uow.voice_jobs.get(job.id)
        assert still is not None
        assert still.status == VoiceJobStatus.PROCESSING
        await uow.voice_jobs.claim(job.id, now + timedelta(seconds=451))
        await uow.commit()
    with pytest.raises(StaleAttemptError, match="stale"):
        await complete(1, False)
    await complete(2, False)
    async with postgres_uows() as uow:
        finished = await uow.voice_jobs.get(job.id)
        assert finished is not None
        assert finished.status == VoiceJobStatus.DONE
        assert len(await all_tasks(uow, job.user_id)) == 1


async def test_duplicate_text_creation_is_atomic(postgres_uows: UnitOfWorkFactory) -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    extraction = Extraction(
        task=TaskDraft(title="Call home"), extractor=ExtractorName.HEURISTIC, degraded=False
    )
    create = CreateTasksFromMessage(
        postgres_uows,
        FakeExtractor(extraction, Calls()),
        RegisterOrGetUser(postgres_uows, clock),
        clock,
    )
    cmd = CreateTasksCommand(
        TelegramIdentity(7, "A"), MessageRef(7, 1), MessageKind.TEXT, "Call home"
    )
    async with asyncio.TaskGroup() as group:
        a = group.create_task(create.execute(cmd))
        b = group.create_task(create.execute(cmd))
    assert sum(isinstance(t.result(), DuplicateMessage) for t in (a, b)) == 1
    results = (a.result(), b.result())
    winner = next(r for r in results if not isinstance(r, DuplicateMessage))
    assert len(winner.tasks) == len(winner.events) == 1


async def test_status_microseconds_ownership_and_core_rows(
    postgres_uows: UnitOfWorkFactory,
) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    async with postgres_uows() as uow:
        mid = await create_message(uow, now)
        message = await uow.messages.get(mid)
        assert message is not None
        tasks = await uow.tasks.add_many(message.user_id, mid, (TaskDraft(title="Call home"),), now)
        assert is_dataclass(tasks[0])
        await uow.commit()
    change = ChangeTaskStatus(postgres_uows, FakeClock(now))
    a = await change.execute(message.user_id, tasks[0].id, TaskStatus.IN_PROGRESS)
    b = await change.execute(message.user_id, tasks[0].id, TaskStatus.COMPLETED)
    assert b.task.updated_at - a.task.updated_at == timedelta(microseconds=1)
    async with postgres_uows() as uow:
        assert await uow.tasks.get_for_update(999, tasks[0].id) is None
        assert await uow.tasks.get_for_update(message.user_id, tasks[0].id) == b.task
    assert (
        await ListTasks(postgres_uows).execute(999, status=None, after=None, limit=50)
    ).items == ()
    with pytest.raises(TaskNotFoundError):
        await change.execute(999, tasks[0].id, TaskStatus.PENDING)

    async with asyncio.TaskGroup() as group:
        left = group.create_task(change.execute(message.user_id, tasks[0].id, TaskStatus.PENDING))
        right = group.create_task(
            change.execute(message.user_id, tasks[0].id, TaskStatus.IN_PROGRESS)
        )
    ordered = sorted((left.result().task, right.result().task), key=lambda task: task.updated_at)
    assert ordered[1].updated_at - ordered[0].updated_at == timedelta(microseconds=1)
    async with postgres_uows() as uow:
        assert await uow.tasks.get_for_update(message.user_id, tasks[0].id) == ordered[1]


async def test_concurrent_voice_duplicates(postgres_uows: UnitOfWorkFactory) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    async with postgres_uows() as uow:
        user = await uow.users.upsert(TelegramIdentity(7, "A"), now)
        await uow.commit()
    new = NewVoiceJob(user.user.id, MessageRef(7, 10), "file", "unique", 5, None)

    async def add() -> tuple[int, bool]:
        async with postgres_uows() as uow:
            result = await uow.voice_jobs.add_or_get(new, now)
            await uow.commit()
            return result.job.id, result.created

    async with asyncio.TaskGroup() as group:
        a = group.create_task(add())
        b = group.create_task(add())
    assert a.result()[0] == b.result()[0]
    assert sum((a.result()[1], b.result()[1])) == 1


async def test_core_repositories_leave_the_identity_map_empty(
    sql_sessions: async_sessionmaker[AsyncSession], clean_database: None
) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    async with SqlAlchemyUnitOfWork(sql_sessions) as uow:
        mid = await create_message(uow, now)
        message = await uow.messages.get(mid)
        assert message is not None
        await uow.tasks.add_many(message.user_id, mid, (TaskDraft(title="Call home"),), now)
        await create_voice(uow, now)
        assert not uow.session.identity_map
        await uow.commit()
        assert not uow.session.identity_map
