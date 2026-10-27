from datetime import UTC, datetime

import pytest

from taskbot.application.recover_voice_jobs import RecoverStuckVoiceJobs
from taskbot.domain.jobs import NotificationJob, TranscriptionJob
from taskbot.domain.voice_jobs import FailureKind, TranscriptionProvider
from tests.contract.helpers import create_voice
from tests.fakes.clock import FakeClock
from tests.fakes.messaging import FakeJobQueue
from tests.fakes.persistence import FakeStore


async def test_sweep_commits_stamps_before_enqueue_and_queue_failures_are_recoverable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = FakeStore()
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    queue = FakeJobQueue(calls=store.calls, fail=True)
    async with store() as uow:
        await create_voice(uow, clock.now())
        await uow.commit()
    clock.advance(461)
    use_case = RecoverStuckVoiceJobs(store, queue, clock)
    store.calls.items.clear()
    await use_case.execute()
    assert store.calls.items == ["uow.open", "uow.commit", "uow.close", "queue.enqueue"]
    assert store.state.voice_jobs[1].last_enqueued_at == clock.now()
    assert store.state.voice_jobs[1].attempts == 0
    assert "EnqueueError" in caplog.text
    queue.fail = False
    await use_case.execute()
    assert not queue.jobs
    clock.advance(461)
    await use_case.execute()
    assert queue.jobs == [(TranscriptionJob(voice_job_id=1), 0)]


async def test_terminal_stamp_enqueues_status_and_notified_jobs_are_left_alone() -> None:
    store = FakeStore()
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    queue = FakeJobQueue()
    async with store() as uow:
        row = await create_voice(uow, clock.now())
        await uow.voice_jobs.claim(row.id, clock.now())
        await uow.voice_jobs.fail(
            row.id, 1, FailureKind.INTERNAL, TranscriptionProvider.FAKE, clock.now()
        )
        await uow.commit()
    clock.advance(121)
    await RecoverStuckVoiceJobs(store, queue, clock).execute()
    assert queue.jobs == [(NotificationJob(voice_job_id=1, status="failed"), 0)]
