from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from taskbot.application.notify_user import (
    Deferred,
    Dropped,
    Finished,
    NotDue,
    NotifyUser,
    Retrying,
)
from taskbot.application.replies import DEGRADED_LINE, VOICE_FAILURE_TEXT, VOICE_UNAVAILABLE
from taskbot.domain.errors import (
    MessageGoneError,
    NotificationError,
    RecipientBlockedError,
    RetryAfterError,
)
from taskbot.domain.extraction import ExtractorName, TaskDraft
from taskbot.domain.jobs import NotificationJob
from taskbot.domain.voice_jobs import (
    FailureKind,
    NotificationOutcome,
    TranscriptionProvider,
    VoiceJobStatus,
)
from tests.contract.helpers import create_message, create_voice
from tests.fakes.clock import FakeClock
from tests.fakes.messaging import FakeJobQueue
from tests.fakes.persistence import FakeStore
from tests.fakes.telegram import FakeUserNotifier


async def fixture(
    *, ack: int | None = 99, failed: bool = False, degraded: bool = False
) -> tuple[NotifyUser, FakeStore, FakeClock, FakeJobQueue, FakeUserNotifier]:
    store = FakeStore()
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    queue = FakeJobQueue(calls=store.calls)
    notifier = FakeUserNotifier(calls=store.calls)
    async with store() as uow:
        row = await create_voice(uow, clock.now())
        if ack is not None:
            await uow.voice_jobs.set_ack_message_id(row.id, ack, clock.now())
        await uow.voice_jobs.claim(row.id, clock.now())
        if failed:
            await uow.voice_jobs.fail(
                row.id, 1, FailureKind.PROVIDER_UNAVAILABLE, TranscriptionProvider.FAKE, clock.now()
            )
        else:
            mid = await create_message(uow, clock.now())
            await uow.tasks.add_many(row.user_id, mid, (TaskDraft(title="Call home"),), clock.now())
            await uow.voice_jobs.complete(row.id, 1, mid, TranscriptionProvider.FAKE, clock.now())
        await uow.commit()
    if degraded:
        store.state.messages[1] = replace(
            store.state.messages[1], extractor=ExtractorName.HEURISTIC, degraded=True
        )
    store.calls.items.clear()
    return NotifyUser(store, notifier, queue, clock), store, clock, queue, notifier


@pytest.mark.parametrize("degraded", [False, True])
async def test_done_edit_uses_snapshot_outside_transaction(degraded: bool) -> None:
    use_case, store, _, queue, notifier = await fixture(degraded=degraded)
    result = await use_case.execute(NotificationJob(voice_job_id=1, status="done"))
    assert result == Finished(NotificationOutcome.EDITED)
    assert notifier.edits == [
        (7, 99, "Task created: Call home (demo mode)" + ("\n" + DEGRADED_LINE if degraded else ""))
    ]
    assert store.state.voice_jobs[1].notification_outcome == NotificationOutcome.EDITED
    assert not queue.jobs
    assert store.calls.items == [
        "uow.open",
        "voice_jobs.begin_notification",
        "messages.get",
        "tasks.list_for_message",
        "uow.commit",
        "uow.close",
        "notifier.edit_message",
        "uow.open",
        "voice_jobs.finish_notification",
        "uow.commit",
        "uow.close",
    ]


@pytest.mark.parametrize("ack", [None, 99])
async def test_missing_or_gone_ack_replies_to_original_voice(ack: int | None) -> None:
    use_case, store, _, _, notifier = await fixture(ack=ack)
    notifier.failures = {1: MessageGoneError("private")} if ack else {}
    assert await use_case.execute(NotificationJob(voice_job_id=1, status="done")) == Finished(
        NotificationOutcome.REPLIED
    )
    assert notifier.replies[0].reply_to_message_id == 10
    assert store.state.voice_jobs[1].notify_attempts == 1


@pytest.mark.parametrize("ack", [None, 99])
async def test_blocked_recipient_is_terminal(ack: int | None) -> None:
    use_case, store, _, _, notifier = await fixture(ack=ack)
    notifier.failures = {1: RecipientBlockedError("private")}
    assert await use_case.execute(NotificationJob(voice_job_id=1, status="done")) == Finished(
        NotificationOutcome.BLOCKED
    )
    assert store.state.voice_jobs[1].notified_at is not None


async def test_flood_wait_not_due_and_queue_clamp_do_not_spend_extra_attempts() -> None:
    use_case, store, clock, queue, notifier = await fixture()
    notifier.failures = {1: RetryAfterError(902)}
    job = NotificationJob(voice_job_id=1, status="done")
    assert isinstance(await use_case.execute(job), Deferred)
    assert queue.jobs == [(job, 902)]
    assert store.state.voice_jobs[1].notify_not_before == clock.now() + timedelta(seconds=902)
    clock.advance(600)
    assert isinstance(await use_case.execute(job), NotDue)
    assert queue.jobs[-1] == (job, 302)
    assert store.state.voice_jobs[1].notify_attempts == 1
    clock.advance(302)
    assert isinstance(await use_case.execute(job), Finished)


async def test_defer_uses_monotonic_persisted_due_time(monkeypatch: pytest.MonkeyPatch) -> None:
    use_case, store, clock, queue, notifier = await fixture()
    original = notifier.edit_message

    async def another_copy_defers(chat: int, message: int, text: str) -> None:
        async with store() as uow:
            await uow.voice_jobs.defer_notification(
                1, clock.now() + timedelta(seconds=1000), clock.now()
            )
            await uow.commit()
        await original(chat, message, text)

    monkeypatch.setattr(notifier, "edit_message", another_copy_defers)
    notifier.failures = {1: RetryAfterError(10)}
    assert isinstance(
        await use_case.execute(NotificationJob(voice_job_id=1, status="done")), Deferred
    )
    assert queue.jobs[0][1] == 1000


@pytest.mark.parametrize("error", [NotificationError("private"), RuntimeError("private")])
async def test_notification_retry_ceiling_and_safe_logs(
    error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    use_case, store, _, queue, notifier = await fixture()
    notifier.failures = dict.fromkeys(range(1, 6), error)
    job = NotificationJob(voice_job_id=1, status="done")
    for attempt in range(1, 5):
        assert isinstance(await use_case.execute(job), Retrying)
        assert queue.jobs[-1] == (job, 2**attempt)
    assert await use_case.execute(job) == Finished(NotificationOutcome.GIVEN_UP)
    assert store.state.voice_jobs[1].notify_attempts == 5
    assert store.state.voice_jobs[1].notified_at is not None
    assert "private" not in caplog.text
    assert isinstance(await use_case.execute(job), Dropped)
    assert len(queue.jobs) == 4


async def test_fifth_flood_wait_finishes_given_up() -> None:
    use_case, store, clock, queue, notifier = await fixture()
    notifier.failures = dict.fromkeys(range(1, 6), RetryAfterError(2))
    job = NotificationJob(voice_job_id=1, status="done")
    for _ in range(4):
        assert isinstance(await use_case.execute(job), Deferred)
        clock.advance(2)
    assert await use_case.execute(job) == Finished(NotificationOutcome.GIVEN_UP)
    assert len(queue.jobs) == 4
    assert store.state.voice_jobs[1].notify_attempts == 5


async def test_crashed_fifth_attempt_is_finished_without_sending() -> None:
    use_case, store, _, queue, notifier = await fixture()
    store.state.voice_jobs[1] = replace(store.state.voice_jobs[1], notify_attempts=5)
    assert await use_case.execute(NotificationJob(voice_job_id=1, status="done")) == Finished(
        NotificationOutcome.GIVEN_UP
    )
    assert not notifier.edits
    assert not queue.jobs


async def test_deleted_task_keeps_degraded_line_without_demo_suffix() -> None:
    use_case, store, _, _, notifier = await fixture(degraded=True)
    store.state.tasks.clear()
    store.state.messages[1] = replace(store.state.messages[1], text=None)
    await use_case.execute(NotificationJob(voice_job_id=1, status="done"))
    assert notifier.edits[0][2] == "Task created.\n" + DEGRADED_LINE


async def test_failed_row_wins_payload_status(caplog: pytest.LogCaptureFixture) -> None:
    use_case, _, _, _, notifier = await fixture(failed=True)
    with caplog.at_level("INFO"):
        await use_case.execute(NotificationJob(voice_job_id=1, status="done"))
    assert notifier.edits[0][2] == VOICE_UNAVAILABLE
    assert "mismatch" in caplog.text.lower()


@pytest.mark.parametrize(
    ("kind", "text"),
    [
        (
            FailureKind.TOO_LARGE,
            "This voice message is larger than 10 MB. Please send a shorter one.",
        ),
        (
            FailureKind.TOO_LONG,
            "This voice message is longer than 2 minutes. Please send a shorter one.",
        ),
        (
            FailureKind.UNSUPPORTED_AUDIO,
            "I could not read this audio. Please send the voice message again.",
        ),
        (
            FailureKind.EMPTY_TRANSCRIPT,
            "I could not hear any words in this voice message. "
            "Please speak closer to the microphone or type your task.",
        ),
        (
            FailureKind.TRANSCRIPTION_TIMEOUT,
            "This recording took too long to process. Please send a shorter voice message.",
        ),
        (
            FailureKind.PROVIDER_UNAVAILABLE,
            "Transcription is unavailable right now. "
            "Your message was not lost: please type it as text.",
        ),
        (
            FailureKind.TRANSIENT_EXHAUSTED,
            "I could not transcribe this voice message after several attempts. "
            "Please send it again later.",
        ),
        (
            FailureKind.INTERNAL,
            "Something went wrong while processing this voice message. Please send it again.",
        ),
        (
            FailureKind.STUCK,
            "Something went wrong while processing this voice message. Please send it again.",
        ),
    ],
)
async def test_failure_notifications_use_design_text(kind: FailureKind, text: str) -> None:
    use_case, store, _, _, notifier = await fixture(failed=True)
    store.state.voice_jobs[1] = replace(store.state.voice_jobs[1], failure_kind=kind)

    assert await use_case.execute(NotificationJob(voice_job_id=1, status="failed")) == Finished(
        NotificationOutcome.EDITED
    )
    assert notifier.edits == [(7, 99, text)]


def test_failure_text_mapping_covers_every_failure_kind() -> None:
    assert set(VOICE_FAILURE_TEXT) == set(FailureKind)


async def test_missing_and_nonterminal_rows_drop_without_send() -> None:
    use_case, store, _, _, notifier = await fixture()
    assert isinstance(
        await use_case.execute(NotificationJob(voice_job_id=99, status="done")), Dropped
    )
    store.state.voice_jobs[1] = replace(store.state.voice_jobs[1], status=VoiceJobStatus.QUEUED)
    assert isinstance(
        await use_case.execute(NotificationJob(voice_job_id=1, status="done")), Dropped
    )
    assert not notifier.edits


async def test_enqueue_failure_keeps_retry_attempt_for_sweep(
    caplog: pytest.LogCaptureFixture,
) -> None:
    use_case, store, clock, queue, notifier = await fixture()
    queue.fail = True
    notifier.failures = {1: NotificationError("private")}
    assert isinstance(
        await use_case.execute(NotificationJob(voice_job_id=1, status="done")), Retrying
    )
    assert store.state.voice_jobs[1].notify_attempts == 1
    clock.advance(121)
    async with store() as uow:
        assert [row.id for row in await uow.voice_jobs.stamp_unnotified(clock.now())] == [1]
    assert "EnqueueError" in caplog.text
    assert "private" not in caplog.text
