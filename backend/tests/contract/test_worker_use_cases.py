from datetime import datetime

import pytest

from taskbot.application.create_tasks import CreateTasksFromMessage
from taskbot.application.notify_user import Finished, NotifyUser
from taskbot.application.recover_voice_jobs import RecoverStuckVoiceJobs
from taskbot.application.register_user import RegisterOrGetUser
from taskbot.application.transcribe_voice import (
    AttemptProgress,
    Done,
    Dropped,
    TranscribeVoiceJob,
    Waiting,
)
from taskbot.domain.errors import NotificationError
from taskbot.domain.extraction import Extraction, ExtractorName, TaskDraft
from taskbot.domain.jobs import NotificationJob, TranscriptionJob
from taskbot.domain.messages import MessageRef
from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.domain.ports.transcription import Transcript
from taskbot.domain.users import TelegramIdentity
from taskbot.domain.voice_jobs import (
    FailureKind,
    NewVoiceJob,
    NotificationOutcome,
    TranscriptionProvider,
    VoiceJob,
    VoiceJobStatus,
)
from tests.audio import synthetic_opus
from tests.contract.helpers import all_tasks, create_message
from tests.fakes.calls import Calls
from tests.fakes.clock import FakeClock
from tests.fakes.extraction import FakeExtractor
from tests.fakes.messaging import FakeEventPublisher, FakeJobQueue
from tests.fakes.telegram import FakeUserNotifier, FakeVoiceFileSource
from tests.fakes.transcription import ScriptedTranscriber


async def add_voice(uows: UnitOfWorkFactory, clock: FakeClock, message_id: int) -> VoiceJob:
    async with uows() as uow:
        user = await uow.users.upsert(TelegramIdentity(7, "A"), clock.now())
        added = await uow.voice_jobs.add_or_get(
            NewVoiceJob(user.user.id, MessageRef(7, message_id), "file", "unique", 5, None),
            clock.now(),
        )
        await uow.commit()
    return added.job


def worker(
    uows: UnitOfWorkFactory, clock: FakeClock, queue: FakeJobQueue
) -> tuple[TranscribeVoiceJob, ScriptedTranscriber]:
    extractor = FakeExtractor(
        Extraction(
            task=TaskDraft(title="Call home"), extractor=ExtractorName.HEURISTIC, degraded=False
        ),
        Calls(),
    )
    transcriber = ScriptedTranscriber(
        [Transcript("Call home", TranscriptionProvider.FAKE) for _ in range(5)]
    )
    return (
        TranscribeVoiceJob(
            uows,
            CreateTasksFromMessage(uows, extractor, RegisterOrGetUser(uows, clock), clock),
            FakeVoiceFileSource(synthetic_opus(1)),
            transcriber,
            extractor,
            FakeUserNotifier(),
            queue,
            FakeEventPublisher(),
            clock,
        ),
        transcriber,
    )


async def test_sweep_repairs_three_crash_points_and_leaves_young_queued_job(
    uows: UnitOfWorkFactory, now: datetime
) -> None:
    clock = FakeClock(now)
    queue = FakeJobQueue()
    queued = await add_voice(uows, clock, 10)
    processing = await add_voice(uows, clock, 11)
    done = await add_voice(uows, clock, 12)
    async with uows() as uow:
        await uow.voice_jobs.claim(processing.id, clock.now())
        await uow.voice_jobs.claim(done.id, clock.now())
        message_id = await create_message(uow, clock.now())
        await uow.voice_jobs.complete(
            done.id, 1, message_id, TranscriptionProvider.FAKE, clock.now()
        )
        await uow.commit()
    clock.advance(300)
    young = await add_voice(uows, clock, 13)
    clock.advance(161)
    sweep = RecoverStuckVoiceJobs(uows, queue, clock)
    await sweep.execute()
    assert queue.jobs == [
        (TranscriptionJob(voice_job_id=processing.id), 0),
        (TranscriptionJob(voice_job_id=queued.id), 0),
        (NotificationJob(voice_job_id=done.id, status="done"), 0),
    ]
    async with uows() as uow:
        for original in (queued, processing, done):
            row = await uow.voice_jobs.get(original.id)
            assert row is not None
            assert row.last_enqueued_at == clock.now()
            assert row.attempts == (1 if original.id in (processing.id, done.id) else 0)
        assert await uow.voice_jobs.get(young.id) == young
    await sweep.execute()
    assert len(queue.jobs) == 3


async def test_two_job_timeline_preserves_recent_claim_and_duplicate_extra_copy_is_noop(
    uows: UnitOfWorkFactory, now: datetime, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock(now)
    queue = FakeJobQueue()
    first = await add_voice(uows, clock, 10)
    second = await add_voice(uows, clock, 11)
    third = await add_voice(uows, clock, 12)
    use_case, transcriber = worker(uows, clock, queue)
    original = transcriber.transcribe

    async def finishes_at_400(audio: bytes) -> Transcript:
        clock.advance(400)
        return await original(audio)

    monkeypatch.setattr(transcriber, "transcribe", finishes_at_400)
    assert isinstance(
        await use_case.execute(TranscriptionJob(voice_job_id=first.id), AttemptProgress()), Done
    )
    monkeypatch.setattr(transcriber, "transcribe", original)
    async with uows() as uow:
        await uow.voice_jobs.claim(second.id, clock.now())
        await uow.commit()
    clock.advance(80)
    queue.jobs.clear()
    await RecoverStuckVoiceJobs(uows, queue, clock).execute()
    assert queue.jobs == [(TranscriptionJob(voice_job_id=third.id), 0)]
    job = TranscriptionJob(voice_job_id=third.id)
    assert isinstance(await use_case.execute(job, AttemptProgress()), Done)
    assert isinstance(await use_case.execute(job, AttemptProgress()), Dropped)
    async with uows() as uow:
        recent = await uow.voice_jobs.get(second.id)
        assert recent is not None
        assert recent.status == VoiceJobStatus.PROCESSING
        assert recent.attempts == 1
        assert len(await all_tasks(uow, first.user_id)) == 2


async def test_live_lease_redelivery_waits_then_creates_one_task_set(
    uows: UnitOfWorkFactory, now: datetime
) -> None:
    clock = FakeClock(now)
    queue = FakeJobQueue()
    row = await add_voice(uows, clock, 10)
    async with uows() as uow:
        await uow.voice_jobs.claim(row.id, clock.now())
        await uow.commit()
    use_case, _ = worker(uows, clock, queue)
    job = TranscriptionJob(voice_job_id=row.id)
    clock.advance(100)
    progress = AttemptProgress()
    assert await use_case.execute(job, progress) == Waiting(350)
    assert progress.attempt is None
    clock.advance(351)
    assert isinstance(await use_case.execute(job, progress), Done)
    assert isinstance(await use_case.execute(job, AttemptProgress()), Dropped)
    async with uows() as uow:
        assert len(await all_tasks(uow, row.user_id)) == 1
        stored = await uow.voice_jobs.get(row.id)
        assert stored is not None
        assert stored.attempts == 2


async def test_five_notification_failures_finish_and_are_never_swept(
    uows: UnitOfWorkFactory, now: datetime
) -> None:
    clock = FakeClock(now)
    queue = FakeJobQueue()
    row = await add_voice(uows, clock, 10)
    async with uows() as uow:
        await uow.voice_jobs.claim(row.id, clock.now())
        await uow.voice_jobs.fail(
            row.id, 1, FailureKind.INTERNAL, TranscriptionProvider.FAKE, clock.now()
        )
        await uow.commit()
    notifier = FakeUserNotifier(failures=dict.fromkeys(range(1, 6), NotificationError("private")))
    notify = NotifyUser(uows, notifier, queue, clock)
    for attempt in range(1, 6):
        result = await notify.execute(NotificationJob(voice_job_id=row.id, status="failed"))
        clock.advance(2**attempt)
    assert result == Finished(NotificationOutcome.GIVEN_UP)
    assert [delay for _, delay in queue.jobs] == [2, 4, 8, 16]
    queue.jobs.clear()
    clock.advance(1000)
    await RecoverStuckVoiceJobs(uows, queue, clock).execute()
    assert not queue.jobs
    async with uows() as uow:
        stored = await uow.voice_jobs.get(row.id)
        assert stored is not None
        assert stored.notify_attempts == 5
        assert stored.notification_outcome == NotificationOutcome.GIVEN_UP
