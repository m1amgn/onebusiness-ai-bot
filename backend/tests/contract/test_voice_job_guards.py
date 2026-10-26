from datetime import datetime, timedelta

from taskbot.domain.messages import MessageRef
from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.domain.voice_jobs import (
    Claimed,
    FailureKind,
    NewVoiceJob,
    NotificationOutcome,
    NotificationStarted,
    TranscriptionProvider,
    VoiceJobStatus,
)
from tests.contract.helpers import create_message, create_voice


async def test_transition_timestamps_and_terminal_absorption(
    uows: UnitOfWorkFactory, now: datetime
) -> None:
    async with uows() as uow:
        job = await create_voice(uow, now)
        assert job.last_enqueued_at == job.updated_at == now
        ack_time = now + timedelta(seconds=1)
        assert await uow.voice_jobs.set_ack_message_id(job.id, 9, ack_time)
        acknowledged = await uow.voice_jobs.get(job.id)
        assert acknowledged is not None
        assert acknowledged.updated_at == ack_time
        claimed = await uow.voice_jobs.claim(job.id, now + timedelta(seconds=2))
        assert isinstance(claimed, Claimed)
        assert claimed.job.updated_at == now + timedelta(seconds=2)
        requeue_time = now + timedelta(seconds=3)
        requeued = await uow.voice_jobs.requeue(job.id, 1, requeue_time)
        assert requeued is not None
        assert requeued.last_enqueued_at == requeued.updated_at == requeue_time
        await uow.voice_jobs.claim(job.id, now + timedelta(seconds=4))
        mid = await create_message(uow, now)
        complete_time = now + timedelta(seconds=5)
        completed = await uow.voice_jobs.complete(
            job.id, 2, mid, TranscriptionProvider.FAKE, complete_time
        )
        assert completed is not None
        assert completed.last_enqueued_at == completed.updated_at == complete_time
        assert (
            await uow.voice_jobs.complete(job.id, 2, mid, TranscriptionProvider.FAKE, complete_time)
            is None
        )
        assert (
            await uow.voice_jobs.fail(
                job.id, 2, FailureKind.INTERNAL, TranscriptionProvider.FAKE, complete_time
            )
            is None
        )
        assert await uow.voice_jobs.requeue(job.id, 2, complete_time) is None
        assert await uow.voice_jobs.lock_attempt(job.id, 2) is None
        assert await uow.voice_jobs.get(job.id) == completed
        terminal_enqueue_time = now + timedelta(seconds=6)
        assert await uow.voice_jobs.mark_enqueued(job.id, terminal_enqueue_time)
        marked = await uow.voice_jobs.get(job.id)
        assert marked is not None
        assert marked.status == VoiceJobStatus.DONE
        assert marked.last_enqueued_at == marked.updated_at == terminal_enqueue_time
        await uow.commit()


async def test_notification_stamps_recovery_threshold_and_finished_guard(
    uows: UnitOfWorkFactory, now: datetime
) -> None:
    async with uows() as uow:
        job = await create_voice(uow, now)
        await uow.voice_jobs.claim(job.id, now)
        failed_at = now + timedelta(seconds=1)
        failed = await uow.voice_jobs.fail(
            job.id, 1, FailureKind.INTERNAL, TranscriptionProvider.FAKE, failed_at
        )
        assert failed is not None
        assert failed.last_enqueued_at == failed.updated_at == failed_at
        assert await uow.voice_jobs.mark_enqueued(job.id, now + timedelta(seconds=2))
        begun_at = now + timedelta(seconds=3)
        begun = await uow.voice_jobs.begin_notification(job.id, begun_at)
        assert isinstance(begun, NotificationStarted)
        assert begun.job.last_enqueued_at == begun.job.updated_at == begun_at
        for seconds in (0, 119, 120):
            assert (
                await uow.voice_jobs.stamp_unnotified(begun_at + timedelta(seconds=seconds)) == []
            )
        recovered_at = begun_at + timedelta(seconds=121)
        assert [j.id for j in await uow.voice_jobs.stamp_unnotified(recovered_at)] == [job.id]
        recovered = await uow.voice_jobs.get(job.id)
        assert recovered is not None
        assert recovered.last_enqueued_at == recovered.updated_at == recovered_at
        deferred_at = recovered_at + timedelta(seconds=1)
        until = deferred_at + timedelta(seconds=902)
        assert await uow.voice_jobs.defer_notification(job.id, until, deferred_at)
        deferred = await uow.voice_jobs.get(job.id)
        assert deferred is not None
        assert deferred.last_enqueued_at == deferred.updated_at == deferred_at
        finished_at = deferred_at + timedelta(seconds=1)
        finished = await uow.voice_jobs.finish_notification(
            job.id, NotificationOutcome.REPLIED, finished_at
        )
        assert finished is not None
        assert finished.updated_at == finished_at
        assert not await uow.voice_jobs.defer_notification(job.id, until, finished_at)
        assert await uow.voice_jobs.get(job.id) == finished
        await uow.commit()


async def test_recent_claim_is_not_swept_even_with_an_old_enqueue_stamp(
    uows: UnitOfWorkFactory, now: datetime
) -> None:
    async with uows() as uow:
        job = await create_voice(uow, now)
        claimed = await uow.voice_jobs.claim(job.id, now + timedelta(seconds=451))
        assert isinstance(claimed, Claimed)
        assert claimed.job.last_enqueued_at == now
        assert await uow.voice_jobs.stamp_stale_processing(now + timedelta(seconds=461)) == []


async def test_an_expired_attempt_below_ceiling_can_be_reclaimed(
    uows: UnitOfWorkFactory, now: datetime
) -> None:
    async with uows() as uow:
        job = await create_voice(uow, now)
        await uow.voice_jobs.claim(job.id, now)
        expired_at = now + timedelta(seconds=451)
        assert await uow.voice_jobs.exhaust(job.id, expired_at) is None
        assert isinstance(await uow.voice_jobs.claim(job.id, expired_at), Claimed)


async def test_new_voice_metadata_is_not_taken_from_an_existing_row(
    uows: UnitOfWorkFactory, now: datetime
) -> None:
    async with uows() as uow:
        first = await create_voice(uow, now)
        later = now + timedelta(seconds=1)
        added = await uow.voice_jobs.add_or_get(
            NewVoiceJob(first.user_id, MessageRef(7, 11), "b", "b", 5, None), later
        )
        assert added.created
        assert added.job.last_enqueued_at == added.job.updated_at == later
