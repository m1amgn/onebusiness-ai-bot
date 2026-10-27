from dataclasses import replace
from datetime import UTC, datetime

import pytest

from taskbot.application.recover_voice_jobs import RecoverStuckVoiceJobs
from taskbot.domain.jobs import NotificationJob, TranscriptionJob
from taskbot.domain.voice_jobs import FailureKind, NewVoiceJob, TranscriptionProvider
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


async def test_first_enqueue_failure_does_not_skip_other_stamped_rows(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    store = FakeStore()
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    queue = FakeJobQueue(calls=store.calls)
    async with store() as uow:
        processing = await create_voice(uow, clock.now())
        template = NewVoiceJob(processing.user_id, processing.ref, "file", "unique", 5, None)
        queued = (
            await uow.voice_jobs.add_or_get(
                replace(template, ref=replace(template.ref, message_id=11)), clock.now()
            )
        ).job
        terminal = (
            await uow.voice_jobs.add_or_get(
                replace(template, ref=replace(template.ref, message_id=12)), clock.now()
            )
        ).job
        await uow.voice_jobs.claim(processing.id, clock.now())
        await uow.voice_jobs.claim(terminal.id, clock.now())
        await uow.voice_jobs.fail(
            terminal.id, 1, FailureKind.INTERNAL, TranscriptionProvider.FAKE, clock.now()
        )
        await uow.commit()
    clock.advance(461)
    original = queue.enqueue
    attempted: list[TranscriptionJob | NotificationJob] = []

    async def fail_first(
        job: TranscriptionJob | NotificationJob, *, delay_seconds: int = 0
    ) -> None:
        attempted.append(job)
        queue.fail = len(attempted) == 1
        await original(job, delay_seconds=delay_seconds)

    monkeypatch.setattr(queue, "enqueue", fail_first)
    store.calls.items.clear()
    await RecoverStuckVoiceJobs(store, queue, clock).execute()

    assert attempted == [
        TranscriptionJob(voice_job_id=processing.id),
        TranscriptionJob(voice_job_id=queued.id),
        NotificationJob(voice_job_id=terminal.id, status="failed"),
    ]
    assert queue.jobs == [(job, 0) for job in attempted[1:]]
    assert store.calls.items == [
        "uow.open",
        "uow.commit",
        "uow.close",
        "queue.enqueue",
        "queue.enqueue",
        "queue.enqueue",
    ]
    assert all(row.last_enqueued_at == clock.now() for row in store.state.voice_jobs.values())
    assert [row.attempts for row in store.state.voice_jobs.values()] == [1, 0, 1]
    assert "EnqueueError" in caplog.text
