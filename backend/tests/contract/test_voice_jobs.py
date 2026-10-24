from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

import pytest

from taskbot.domain.messages import MessageRef
from taskbot.domain.ports.persistence import UnitOfWorkFactory, VoiceJobRepository
from taskbot.domain.voice_jobs import (
    AlreadyFinished,
    CeilingReached,
    Claimed,
    FailureKind,
    JobMissing,
    LeaseLive,
    NewVoiceJob,
    NotificationCeilingReached,
    NotificationNotDue,
    NotificationOutcome,
    NotificationStarted,
    TranscriptionProvider,
    VoiceJobStatus,
)
from tests.contract.helpers import create_message, create_voice


async def test_voice_identity_retry_complete_and_guards(
    uows: UnitOfWorkFactory, now: datetime
) -> None:
    async with uows() as uow:
        job = await create_voice(uow, now)
        assert await uow.voice_jobs.get(job.id) == job
        assert await uow.voice_jobs.get(999) is None
        duplicate = await uow.voice_jobs.add_or_get(
            NewVoiceJob(job.user_id, MessageRef(7, 10), "other", "other", 9, 10), now
        )
        assert not duplicate.created
        assert duplicate.job == job
        assert await uow.voice_jobs.set_ack_message_id(job.id, 9, now)
        assert not await uow.voice_jobs.set_ack_message_id(job.id, 10, now)
        assert await uow.voice_jobs.mark_enqueued(job.id, now)
        claimed = await uow.voice_jobs.claim(job.id, now)
        assert isinstance(claimed, Claimed)
        assert isinstance(await uow.voice_jobs.claim(job.id, now), LeaseLive)
        assert isinstance(await uow.voice_jobs.claim(999, now), JobMissing)
        assert await uow.voice_jobs.lock_attempt(job.id, 2) is None
        assert await uow.voice_jobs.lock_attempt(job.id, 1) == claimed.job
        assert await uow.voice_jobs.requeue(job.id, 2, now) is None
        assert await uow.voice_jobs.requeue(job.id, 1, now) is not None
        again = await uow.voice_jobs.claim(job.id, now)
        assert isinstance(again, Claimed)
        assert again.job.attempts == 2
        mid = await create_message(uow, now)
        assert (
            await uow.voice_jobs.complete(job.id, 1, mid, TranscriptionProvider.FAKE, now) is None
        )
        completed = await uow.voice_jobs.complete(job.id, 2, mid, TranscriptionProvider.FAKE, now)
        assert completed is not None
        assert completed.status == VoiceJobStatus.DONE
        assert isinstance(await uow.voice_jobs.claim(job.id, now), AlreadyFinished)
        await uow.commit()


async def test_bounded_claim_and_exhaustion(uows: UnitOfWorkFactory, now: datetime) -> None:
    async with uows() as uow:
        job = await create_voice(uow, now)
        for attempt in range(1, 6):
            outcome = await uow.voice_jobs.claim(job.id, now)
            assert isinstance(outcome, Claimed)
            assert outcome.job.attempts == attempt
            assert await uow.voice_jobs.exhaust(job.id, now) is None
            now += timedelta(seconds=451)
        assert isinstance(await uow.voice_jobs.claim(job.id, now), CeilingReached)
        exhausted = await uow.voice_jobs.exhaust(job.id, now)
        assert exhausted is not None
        assert exhausted.failure_kind == FailureKind.STUCK
        assert exhausted.last_enqueued_at == now
        assert await uow.voice_jobs.exhaust(job.id, now) is None
        await uow.commit()


async def test_notification_due_time_and_crash_ceiling(
    uows: UnitOfWorkFactory, now: datetime
) -> None:
    async with uows() as uow:
        job = await create_voice(uow, now)
        await uow.voice_jobs.claim(job.id, now)
        assert (
            await uow.voice_jobs.fail(
                job.id, 2, FailureKind.INTERNAL, TranscriptionProvider.FAKE, now
            )
            is None
        )
        assert (
            await uow.voice_jobs.fail(
                job.id, 1, FailureKind.INTERNAL, TranscriptionProvider.FAKE, now
            )
            is not None
        )
        first = await uow.voice_jobs.begin_notification(job.id, now)
        assert isinstance(first, NotificationStarted)
        due = now + timedelta(seconds=902)
        assert await uow.voice_jobs.defer_notification(job.id, due, now)
        assert await uow.voice_jobs.defer_notification(job.id, due - timedelta(seconds=10), now)
        assert not await uow.voice_jobs.defer_notification(job.id, now - timedelta(seconds=1), now)
        for offset in (180, 360, 540, 720, 900):
            assert await uow.voice_jobs.stamp_unnotified(now + timedelta(seconds=offset)) == []
        assert await uow.voice_jobs.begin_notification(
            job.id, now + timedelta(seconds=500)
        ) == NotificationNotDue(402)
        for attempt in range(2, 6):
            started = await uow.voice_jobs.begin_notification(job.id, due)
            assert isinstance(started, NotificationStarted)
            assert started.job.notify_attempts == attempt
        later = due + timedelta(seconds=121)
        assert [j.id for j in await uow.voice_jobs.stamp_unnotified(later)] == [job.id]
        assert isinstance(
            await uow.voice_jobs.begin_notification(job.id, later), NotificationCeilingReached
        )
        ended = await uow.voice_jobs.finish_notification(
            job.id, NotificationOutcome.GIVEN_UP, later
        )
        assert ended is not None
        assert await uow.voice_jobs.stamp_unnotified(later + timedelta(seconds=121)) == []
        assert not await uow.voice_jobs.mark_enqueued(job.id, later)
        assert (
            await uow.voice_jobs.finish_notification(job.id, NotificationOutcome.EDITED, later)
            is None
        )
        await uow.commit()


async def test_queued_and_processing_sweep_stamps(uows: UnitOfWorkFactory, now: datetime) -> None:
    async with uows() as uow:
        job = await create_voice(uow, now)
        stale = now + timedelta(seconds=461)
        assert await uow.voice_jobs.stamp_stale_queued(stale) == [job.id]
        assert await uow.voice_jobs.stamp_stale_queued(stale) == []
        await uow.voice_jobs.claim(job.id, stale)
        later = stale + timedelta(seconds=461)
        assert await uow.voice_jobs.stamp_stale_processing(later) == [job.id]
        assert await uow.voice_jobs.stamp_stale_processing(later) == []
        await uow.commit()


async def test_voice_repository_refuses_naive_time_even_for_missing_rows(
    uows: UnitOfWorkFactory, now: datetime
) -> None:
    calls: list[Callable[[VoiceJobRepository, datetime], Awaitable[object]]] = [
        lambda r, t: r.set_ack_message_id(999, 1, t),
        lambda r, t: r.mark_enqueued(999, t),
        lambda r, t: r.claim(999, t),
        lambda r, t: r.requeue(999, 1, t),
        lambda r, t: r.complete(999, 1, 1, TranscriptionProvider.FAKE, t),
        lambda r, t: r.fail(999, 1, FailureKind.INTERNAL, TranscriptionProvider.FAKE, t),
        lambda r, t: r.exhaust(999, t),
        lambda r, t: r.begin_notification(999, t),
        lambda r, t: r.defer_notification(999, t, t),
        lambda r, t: r.finish_notification(999, NotificationOutcome.GIVEN_UP, t),
        lambda r, t: r.stamp_stale_queued(t),
        lambda r, t: r.stamp_stale_processing(t),
        lambda r, t: r.stamp_unnotified(t),
    ]
    async with uows() as uow:
        for invoke in calls:
            with pytest.raises(ValueError, match="timezone-aware"):
                await invoke(uow.voice_jobs, now.replace(tzinfo=None))
        with pytest.raises(ValueError, match="timezone-aware"):
            await uow.voice_jobs.defer_notification(999, now.replace(tzinfo=None), now)
